#!/usr/bin/env python3
"""创建、查找、查看、校验或修改 paper-memory profile。

退出码：0 成功；1 profile 无效或拒绝写入；2 参数错误；3 未找到 profile。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Optional

from profile_config import (
    DEFAULTS, PROFILE_NAME, SETTABLE_KEYS, ProfileError, atomic_write_json, find_profile,
    load_raw, local_memory_root, validate_profile, with_defaults,
)


EXIT_INVALID = 1
EXIT_NOT_FOUND = 3
RECOMMENDED_NAMESPACES = ["论文", "方法", "任务", "模态"]


def fail(message: str, code: int = EXIT_INVALID) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return code


def parse_source(value: str) -> dict[str, str]:
    provider, separator, location = value.partition("=")
    if not separator or not provider.strip() or not location.strip():
        raise argparse.ArgumentTypeError("论文来源必须写成 provider=location")
    return {"provider": provider.strip(), "location": location.strip()}


def resolve_profile_path(target: Path) -> Optional[Path]:
    target = target.expanduser()
    if target.name == PROFILE_NAME and target.is_file():
        return target.resolve()
    return find_profile(target)


def command_init(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    if output.name != PROFILE_NAME:
        if output.is_file():
            return fail(f"--output 必须是目录或 {PROFILE_NAME} 文件路径：{output}", 2)
        output = output / PROFILE_NAME
    if output.exists() and not args.force:
        return fail(f"profile 已存在，拒绝覆盖：{output}")
    namespaces = args.tag_namespace or (RECOMMENDED_NAMESPACES if args.tag_strategy == "recommended" else [])
    attachments: dict[str, Any] = {"mode": args.attachment_mode}
    if args.attachment_mode == "local":
        attachments.update({"root_pattern": args.attachment_pattern, "link_style": args.attachment_link_style})
    profile = with_defaults({
        "memory": {"provider": args.memory_provider, "location": args.memory_location},
        "papers": args.paper_source,
        "naming": {"note": args.note_naming},
        "links": {"style": args.link_style, "backlinks": args.backlinks, "index": args.index},
        "attachments": attachments,
        "metadata": {
            "fields": args.metadata_field or list(DEFAULTS["metadata"]["fields"]),
            "preserve_unknown_fields": True,
        },
        "tags": {
            "strategy": args.tag_strategy,
            "language": args.tag_language,
            "allowed_namespaces": namespaces,
            "reject_aliases": args.reject_aliases,
        },
        "review": {"persistence": args.review_persistence, "location": args.review_location},
    })
    errors = validate_profile(profile)
    if errors:
        return fail("profile 无效：\n- " + "\n- ".join(errors))
    atomic_write_json(output, profile)
    print(output)
    return 0


def load_target(target: Path) -> tuple[Optional[Path], Optional[dict[str, Any]], int]:
    path = resolve_profile_path(target)
    if path is None:
        return None, None, fail(f"未找到 {PROFILE_NAME}（已从 {target.expanduser().resolve()} 向上查找）", EXIT_NOT_FOUND)
    try:
        return path, load_raw(path), 0
    except ProfileError as error:
        return path, None, fail(str(error))


def report_errors(path: Path, errors: list[str]) -> int:
    return fail(f"profile 无效：{path}\n- " + "\n- ".join(errors))


def command_show(args: argparse.Namespace) -> int:
    path, raw, code = load_target(args.path)
    if raw is None:
        return code
    profile = with_defaults(raw)
    errors = validate_profile(profile)
    if errors:
        return report_errors(path, errors)
    root = local_memory_root(profile, path)
    print(json.dumps(
        {"profile_path": str(path), "memory_root": str(root) if root else None, "profile": profile},
        ensure_ascii=False, indent=2,
    ))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    path, raw, code = load_target(args.path)
    if raw is None:
        return code
    errors = validate_profile(with_defaults(raw))
    if errors:
        return report_errors(path, errors)
    print(f"profile 有效：{path}")
    return 0


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def command_set(args: argparse.Namespace) -> int:
    if args.key not in SETTABLE_KEYS:
        return fail(f"不支持的字段：{args.key}\n可设置：{', '.join(SETTABLE_KEYS)}")
    path, raw, code = load_target(args.path)
    if raw is None:
        return code
    updated = copy.deepcopy(raw)
    value = parse_value(args.value)
    *parents, leaf = args.key.split(".")
    cursor = updated
    for key in parents:
        child = cursor.setdefault(key, {})
        if not isinstance(child, dict):
            return fail(f"{key} 不是对象，无法设置 {args.key}")
        cursor = child
    cursor[leaf] = value
    errors = validate_profile(with_defaults(updated))
    if errors:
        return fail(f"修改后的 profile 无效，未写入 {path}：\n- " + "\n- ".join(errors))
    atomic_write_json(path, updated)
    print(f"已更新 {path}：{args.key} = {json.dumps(value, ensure_ascii=False)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="创建 profile")
    init.add_argument("--output", type=Path, required=True, help="profile 的保存目录或文件路径")
    init.add_argument("--memory-provider", choices=("markdown", "obsidian", "notion", "other"), required=True)
    init.add_argument("--memory-location", required=True)
    init.add_argument("--paper-source", action="append", type=parse_source, required=True, help="provider=location，可重复")
    init.add_argument("--note-naming", default="short-title")
    init.add_argument("--link-style", choices=("markdown", "wiki", "notion", "none"), default="none")
    init.add_argument("--backlinks", choices=("maintain", "suggest", "none"), default="none")
    init.add_argument("--index", help="links.index：索引笔记目录或索引页面标识")
    init.add_argument("--attachment-mode", choices=("local", "platform", "none"), default="none")
    init.add_argument("--attachment-pattern", default="./assets/${noteFileName}")
    init.add_argument("--attachment-link-style", choices=("markdown", "wiki"), default="markdown")
    init.add_argument("--metadata-field", action="append", default=[], help="metadata.fields，可重复；默认 title、date、tags")
    init.add_argument("--tag-strategy", choices=("preserve", "recommended", "custom"), default="preserve")
    init.add_argument("--tag-language")
    init.add_argument("--tag-namespace", action="append", default=[])
    init.add_argument("--reject-aliases", action="store_true")
    init.add_argument("--review-persistence", choices=("body", "metadata", "external", "none"), default="none")
    init.add_argument("--review-location", help="review.location：external 复习记录的保存位置")
    init.add_argument("--force", action="store_true", help="覆盖已有 profile")
    init.set_defaults(func=command_init)

    for name, func, help_text in (
        ("show", command_show, "查找并输出 profile"),
        ("validate", command_validate, "查找并校验 profile"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("path", type=Path, help="Memory 笔记、Memory 目录或 profile 文件")
        command.set_defaults(func=func)

    setting = subparsers.add_parser("set", help="修改单个字段，校验通过后才写入")
    setting.add_argument("path", type=Path, help="Memory 笔记、Memory 目录或 profile 文件")
    setting.add_argument("key", help="点号路径，例如 links.index")
    setting.add_argument("value", help="JSON 值；无法按 JSON 解析时按字符串处理")
    setting.set_defaults(func=command_set)
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    raise SystemExit(arguments.func(arguments))
