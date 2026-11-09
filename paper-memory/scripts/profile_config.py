#!/usr/bin/env python3
"""paper-memory profile 的读取、默认值、校验与查找。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


PROFILE_NAME = ".paper-memory.json"
MEMORY_PROVIDERS = ("markdown", "obsidian", "notion", "other")
LINK_STYLES = ("markdown", "wiki", "notion", "none")
BACKLINK_MODES = ("maintain", "suggest", "none")
ATTACHMENT_MODES = ("local", "platform", "none")
ATTACHMENT_LINK_STYLES = ("markdown", "wiki")
TAG_STRATEGIES = ("preserve", "recommended", "custom")
REVIEW_MODES = ("body", "metadata", "external", "none")
PATTERN_VARIABLES = ("${noteFileName}", "${noteDir}")

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "memory": {"provider": None, "location": None},
    "papers": [],
    "naming": {"note": "short-title"},
    "links": {"style": "none", "backlinks": "none", "index": None},
    "attachments": {"mode": "none", "root_pattern": None, "link_style": None},
    "metadata": {"fields": ["title", "date", "tags"], "preserve_unknown_fields": True},
    "tags": {"strategy": "preserve", "language": None, "allowed_namespaces": [], "reject_aliases": False},
    "review": {"persistence": "none", "location": None},
}
SECTIONS = tuple(key for key, value in DEFAULTS.items() if isinstance(value, dict))
SETTABLE_KEYS = ("papers",) + tuple(f"{section}.{key}" for section in SECTIONS for key in DEFAULTS[section])


class ProfileError(ValueError):
    pass


def load_raw(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"无法读取 profile（必须是 JSON）：{path}：{error}") from error
    if not isinstance(data, dict):
        raise ProfileError(f"profile 根节点必须是对象：{path}")
    return data


def with_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    profile = copy.deepcopy(DEFAULTS)
    for key, value in raw.items():
        if key in SECTIONS and isinstance(value, dict):
            profile[key].update(copy.deepcopy(value))
        else:
            profile[key] = copy.deepcopy(value)
    return profile


def load_profile(path: Path) -> dict[str, Any]:
    return with_defaults(load_raw(path))


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_string(value: Any) -> bool:
    return value is None or _non_empty_string(value)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_non_empty_string(item) for item in value)


def _choices(values: tuple[str, ...]) -> str:
    return "、".join(values)


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(profile) - set(DEFAULTS))
    if unknown:
        errors.append(f"未知字段：{', '.join(unknown)}")
    if profile.get("version") != 1:
        errors.append("version 必须为 1")
    sections: dict[str, dict[str, Any]] = {}
    for name in SECTIONS:
        value = profile.get(name)
        if not isinstance(value, dict):
            errors.append(f"{name} 必须是对象")
            continue
        extra = sorted(set(value) - set(DEFAULTS[name]))
        if extra:
            errors.append(f"{name} 含未知字段：{', '.join(extra)}")
        sections[name] = value

    memory = sections.get("memory")
    if memory is not None:
        if memory.get("provider") not in MEMORY_PROVIDERS:
            errors.append(f"memory.provider 必须是 {_choices(MEMORY_PROVIDERS)} 之一")
        if not _non_empty_string(memory.get("location")):
            errors.append("memory.location 必须是非空字符串")

    papers = profile.get("papers")
    if not isinstance(papers, list) or not papers:
        errors.append("papers 至少需要一个论文来源")
    elif any(
        not isinstance(item, dict)
        or not _non_empty_string(item.get("provider"))
        or not _non_empty_string(item.get("location"))
        for item in papers
    ):
        errors.append("papers 的每一项都需要非空的 provider 和 location")

    naming = sections.get("naming")
    if naming is not None and not _non_empty_string(naming.get("note")):
        errors.append("naming.note 必须是非空字符串")

    links = sections.get("links")
    if links is not None:
        if links.get("style") not in LINK_STYLES:
            errors.append(f"links.style 必须是 {_choices(LINK_STYLES)} 之一")
        if links.get("backlinks") not in BACKLINK_MODES:
            errors.append(f"links.backlinks 必须是 {_choices(BACKLINK_MODES)} 之一")
        if not _optional_string(links.get("index")):
            errors.append("links.index 必须是 null 或非空字符串")

    attachments = sections.get("attachments")
    if attachments is not None:
        mode = attachments.get("mode")
        pattern = attachments.get("root_pattern")
        link_style = attachments.get("link_style")
        if mode not in ATTACHMENT_MODES:
            errors.append(f"attachments.mode 必须是 {_choices(ATTACHMENT_MODES)} 之一")
        if mode == "local":
            if not _non_empty_string(pattern):
                errors.append("attachments.mode 为 local 时必须设置 attachments.root_pattern")
            if link_style not in ATTACHMENT_LINK_STYLES:
                errors.append("attachments.mode 为 local 时，attachments.link_style 必须是 markdown 或 wiki")
        else:
            if not _optional_string(pattern):
                errors.append("attachments.root_pattern 必须是 null 或非空字符串")
            if link_style is not None and link_style not in ATTACHMENT_LINK_STYLES:
                errors.append("attachments.link_style 必须是 null、markdown 或 wiki")
        if isinstance(pattern, str):
            remainder = pattern
            for variable in PATTERN_VARIABLES:
                remainder = remainder.replace(variable, "")
            if "${" in remainder:
                errors.append(f"attachments.root_pattern 只支持变量 {_choices(PATTERN_VARIABLES)}")

    metadata = sections.get("metadata")
    if metadata is not None:
        if not _string_list(metadata.get("fields")):
            errors.append("metadata.fields 必须是字符串列表")
        if not isinstance(metadata.get("preserve_unknown_fields"), bool):
            errors.append("metadata.preserve_unknown_fields 必须是 true 或 false")

    tags = sections.get("tags")
    if tags is not None:
        strategy = tags.get("strategy")
        if strategy not in TAG_STRATEGIES:
            errors.append(f"tags.strategy 必须是 {_choices(TAG_STRATEGIES)} 之一")
        if not _optional_string(tags.get("language")):
            errors.append("tags.language 必须是 null 或非空字符串")
        namespaces = tags.get("allowed_namespaces")
        if not _string_list(namespaces):
            errors.append("tags.allowed_namespaces 必须是字符串列表")
        elif strategy in {"recommended", "custom"} and not namespaces:
            errors.append("tags.strategy 为 recommended 或 custom 时必须设置 tags.allowed_namespaces")
        if not isinstance(tags.get("reject_aliases"), bool):
            errors.append("tags.reject_aliases 必须是 true 或 false")

    review = sections.get("review")
    if review is not None:
        persistence = review.get("persistence")
        if persistence not in REVIEW_MODES:
            errors.append(f"review.persistence 必须是 {_choices(REVIEW_MODES)} 之一")
        if not _optional_string(review.get("location")):
            errors.append("review.location 必须是 null 或非空字符串")
        elif persistence == "external" and review.get("location") is None:
            errors.append("review.persistence 为 external 时必须设置 review.location")
    return errors


def find_profile(start: Path) -> Optional[Path]:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROFILE_NAME
        if candidate.is_file():
            return candidate
    return None


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def local_memory_root(profile: dict[str, Any], profile_path: Path) -> Optional[Path]:
    memory = profile["memory"]
    if memory.get("provider") not in {"markdown", "obsidian"}:
        return None
    location = Path(str(memory["location"])).expanduser()
    if not location.is_absolute():
        location = profile_path.parent / location
    return location.resolve()


def expand_attachment_parent(pattern: str, note: Path) -> Path:
    value = pattern.replace("${noteFileName}", note.stem).replace("${noteDir}", str(note.parent))
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = note.parent / target
    return target.resolve()
