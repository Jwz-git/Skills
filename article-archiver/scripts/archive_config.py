#!/usr/bin/env python3
"""解析文章归档配置，并规划不会静默覆盖的输出路径。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "version": 1,
    "output_dir": "articles",
    "assets": {"mode": "download", "directory": "assets"},
}

ASSET_MODES = ("download", "remote", "omit")
SETTABLE_KEYS = ("output_dir", "assets.mode", "assets.directory")
CONFIG_NAME = ".article-archiver.json"
MAX_NAME_BYTES = 200
INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
EDGE_DOTS_AND_SPACES = re.compile(r"^[\s.]+|[\s.]+$")


class ConfigError(RuntimeError):
    pass


def personal_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if os.path.isabs(base) else Path.home() / ".config"
    return root / "article-archiver" / "config.json"


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"配置根节点必须是对象：{path}")
    return value


def project_config(cwd: Path) -> Path | None:
    current = cwd.resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_NAME
        if candidate.exists():
            return candidate
    return None


def validate(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise ConfigError("目前只支持版本 1 的配置")
    output_dir = config.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ConfigError("output_dir 必须是非空字符串")
    assets = config.get("assets")
    if not isinstance(assets, dict):
        raise ConfigError("assets 必须是对象")
    directory = assets.get("directory")
    if not isinstance(directory, str) or not directory.strip():
        raise ConfigError("assets.directory 必须是非空字符串")
    mode = assets.get("mode")
    if mode not in ASSET_MODES:
        raise ConfigError(f"不支持的 assets.mode：{mode!r}（可选 {'、'.join(ASSET_MODES)}）")


def resolve(cwd: Path) -> tuple[dict[str, Any], Path]:
    personal_path = personal_config_path()
    personal_exists = personal_path.exists()
    project_path = project_config(cwd)
    project = read_json(project_path) if project_path else {}
    config = merge(merge(DEFAULTS, read_json(personal_path)), project)
    validate(config)
    # 项目配置中的相对 output_dir 以配置文件所在目录为基准，其余以 --cwd 为基准。
    base = project_path.parent if project_path and "output_dir" in project else cwd.resolve()
    report = {
        "config": config,
        "configured": personal_exists or project_path is not None,
        "personal_config": {"path": str(personal_path), "exists": personal_exists},
        "project_config": str(project_path) if project_path else None,
    }
    return report, base


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def target_for_scope(scope: str, cwd: Path, prefer_existing: bool) -> Path:
    if scope == "personal":
        return personal_config_path()
    found = project_config(cwd) if prefer_existing else None
    return found or cwd.resolve() / CONFIG_NAME


def set_value(config: dict[str, Any], key: str, value: str) -> None:
    if key not in SETTABLE_KEYS:
        raise ConfigError(f"不支持的配置键：{key}（可设置 {'、'.join(SETTABLE_KEYS)}）")
    if key == "output_dir":
        config["output_dir"] = value
        return
    assets = config.setdefault("assets", {})
    if not isinstance(assets, dict):
        raise ConfigError("assets 必须是对象")
    assets[key.split(".", 1)[1]] = value


def clean_name(value: str) -> str:
    value = INVALID_FILENAME.sub("-", re.sub(r"\s+", " ", value))
    return EDGE_DOTS_AND_SPACES.sub("", value)


def safe_name(value: str) -> tuple[str, bool]:
    name = value.strip()
    if name.lower().endswith(".md"):
        name = name[:-3]
    name = clean_name(name)
    if not name:
        raise ConfigError("名称无法生成有效文件名")
    encoded = name.encode("utf-8")
    if len(encoded) <= MAX_NAME_BYTES:
        return name, False
    return clean_name(encoded[:MAX_NAME_BYTES].decode("utf-8", "ignore")), True


def plan(config: dict[str, Any], base: Path, value: str) -> dict[str, Any]:
    name, truncated = safe_name(value)
    root = (base / Path(config["output_dir"]).expanduser()).resolve()
    markdown = root / f"{name}.md"
    assets_dir = root / config["assets"]["directory"] / name if config["assets"]["mode"] == "download" else None
    targets = [markdown] if assets_dir is None else [markdown, assets_dir]
    return {
        "name": name,
        "truncated": truncated,
        "markdown": str(markdown),
        "assets_dir": str(assets_dir) if assets_dir else None,
        "conflicts": [str(path) for path in targets if os.path.lexists(path)],
    }


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="解析文章归档配置并规划输出路径")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_cwd(command: argparse.ArgumentParser) -> None:
        command.add_argument("--cwd", type=Path, default=Path.cwd())

    add_cwd(sub.add_parser("resolve", help="输出生效配置及配置文件状态"))

    planning = sub.add_parser("plan", help="规划输出路径并检查冲突")
    add_cwd(planning)
    planning.add_argument("--name", required=True, help="原标题或归档文件名")

    init = sub.add_parser("init", help="写入完整配置")
    init.add_argument("--scope", choices=("project", "personal"), required=True)
    add_cwd(init)
    init.add_argument("--output-dir", default=DEFAULTS["output_dir"])
    init.add_argument("--assets", choices=ASSET_MODES, default=DEFAULTS["assets"]["mode"])
    init.add_argument("--assets-directory", default=DEFAULTS["assets"]["directory"])
    init.add_argument("--force", action="store_true")

    setting = sub.add_parser("set", help="更新单项配置")
    setting.add_argument("--scope", choices=("project", "personal"), required=True)
    add_cwd(setting)
    setting.add_argument("key", help="、".join(SETTABLE_KEYS))
    setting.add_argument("value")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "resolve":
            report, _ = resolve(args.cwd)
            emit(report)
        elif args.command == "plan":
            report, base = resolve(args.cwd)
            result = plan(report["config"], base, args.name)
            emit(result)
            return 1 if result["conflicts"] else 0
        elif args.command == "init":
            path = target_for_scope(args.scope, args.cwd, prefer_existing=False)
            if path.exists() and not args.force:
                raise ConfigError(f"拒绝替换已有配置：{path}")
            config = merge(DEFAULTS, {
                "output_dir": args.output_dir,
                "assets": {"mode": args.assets, "directory": args.assets_directory},
            })
            validate(config)
            atomic_write(path, config)
            print(path)
        elif args.command == "set":
            path = target_for_scope(args.scope, args.cwd, prefer_existing=True)
            config = read_json(path)
            set_value(config, args.key, args.value)
            validate(merge(DEFAULTS, config))
            atomic_write(path, config)
            print(path)
        return 0
    except (ConfigError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
