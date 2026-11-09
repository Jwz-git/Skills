#!/usr/bin/env python3
"""只读审计本地 Markdown 论文笔记 frontmatter 中的 tags，规则来自 paper-memory profile。

退出码：0 通过；1 发现错误；2 参数或 profile 错误。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from profile_config import PROFILE_NAME, ProfileError, find_profile, load_profile, validate_profile


KEY_RE = re.compile(r"""^(["']?)([^\s:#"'\-][^:#"']*?)\1\s*:(?:[ \t]+(.*)|$)""")
ITEM_RE = re.compile(r"^[ \t]*-(?:[ \t]+(.*)|[ \t]*$)")


class TagFormatError(ValueError):
    pass


class IncompleteFlow(TagFormatError):
    pass


def read_frontmatter(path: Path) -> tuple[Optional[list[str]], Optional[str]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return None, f"无法以 UTF-8 读取：{error}"
    if not lines or lines[0].strip() != "---":
        return None, None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], None
    return None, "frontmatter 未闭合"


def strip_comment(text: str) -> str:
    quote = None
    previous = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote, previous = None, char
        elif quote == "'":
            if char == "'":
                if text[index + 1:index + 2] == "'":
                    index += 2
                    continue
                quote, previous = None, char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
        elif char in "\"'" and previous in {"", "[", ",", "-", ":", "{"}:
            quote = char
        elif not char.isspace():
            previous = char
        index += 1
    return text.rstrip()


def parse_quoted(text: str) -> tuple[str, str]:
    quote = text[0]
    index = 1
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and text[index + 1:index + 2] == "'":
                index += 2
                continue
            inner, rest = text[1:index], text[index + 1:]
            if quote == "'":
                return inner.replace("''", "'"), rest
            try:
                return json.loads(f'"{inner}"'), rest
            except json.JSONDecodeError:
                return inner, rest
        index += 1
    raise TagFormatError("引号未闭合")


def parse_item(text: str) -> str:
    text = text.strip()
    if not text:
        raise TagFormatError("存在空标签项")
    if text[0] in "\"'":
        value, rest = parse_quoted(text)
        if rest.strip():
            raise TagFormatError(f"标签项格式无法识别：{text}")
        if not value.strip():
            raise TagFormatError("存在空标签项")
        return value
    if text[0] in "[{":
        raise TagFormatError(f"标签项不能是列表或映射：{text}")
    if text[0] in "|>&*!":
        raise TagFormatError(f"标签项格式无法识别：{text}")
    if re.search(r":(\s|$)", text):
        raise TagFormatError(f"标签项不能是映射：{text}")
    return text


def split_flow(text: str) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    quote = None
    depth = 0
    closed_at = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if quote == '"' and char == "\\":
                current.append(text[index + 1:index + 2])
                index += 2
                continue
            if char == quote:
                if quote == "'" and text[index + 1:index + 2] == "'":
                    current.append("'")
                    index += 2
                    continue
                quote = None
        elif char in "\"'" and not "".join(current).strip():
            quote = char
            current.append(char)
        elif char == "[":
            depth += 1
            if depth > 1:
                raise TagFormatError("标签项不能嵌套列表")
        elif char == "]":
            depth -= 1
            if depth == 0:
                closed_at = index
                break
        elif char in "{}":
            raise TagFormatError("标签项不能是映射")
        elif char == "," and depth == 1:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if quote or closed_at is None:
        raise IncompleteFlow("行内列表未闭合")
    if text[closed_at + 1:].strip():
        raise TagFormatError(f"tags 值格式无法识别：{text}")
    pieces.append("".join(current))
    if len(pieces) > 1 and not pieces[-1].strip():
        pieces.pop()
    if len(pieces) == 1 and not pieces[0].strip():
        return []
    return pieces


def parse_flow(first: str, following: list[str]) -> list[str]:
    text = first
    lines = iter(following)
    while True:
        try:
            return [parse_item(piece) for piece in split_flow(text)]
        except IncompleteFlow:
            line = next(lines, None)
            if line is None:
                raise
            text = f"{text} {strip_comment(line)}"


def parse_tags(rest: str, following: list[str]) -> list[str]:
    value = strip_comment(rest or "")
    if value.startswith("["):
        return parse_flow(value, following)
    if value:
        if value[0] in "|>":
            raise TagFormatError("tags 必须是列表，当前为多行字符串")
        raise TagFormatError(f"tags 必须是列表，当前为标量：{value}")
    items: list[str] = []
    for line in following:
        if not line.strip() or line.strip().startswith("#"):
            continue
        match = ITEM_RE.match(line)
        if match:
            items.append(parse_item(strip_comment(match.group(1) or "")))
            continue
        if not line[0].isspace():
            break
        raise TagFormatError(f"tags 下有无法识别的行：{line.strip()}")
    return items


def top_level_keys(lines: list[str]) -> list[tuple[str, int, str]]:
    keys = []
    for index, line in enumerate(lines):
        if not line or line[0].isspace() or line[0] == "#":
            continue
        match = KEY_RE.match(line)
        if match:
            keys.append((match.group(2).strip(), index, match.group(3) or ""))
    return keys


def audit_file(lines: list[str]) -> tuple[Optional[list[str]], list[str], list[str], bool]:
    """返回 (tags 或 None, 错误, 警告, 是否有 aliases 字段)。"""
    keys = top_level_keys(lines)
    names = [name for name, _, _ in keys]
    errors: list[str] = []
    warnings: list[str] = []
    if "tag" in names:
        warnings.append("旧字段 tag 不会被 Obsidian 识别为标签，应使用 tags")
    has_aliases = "aliases" in names
    entries = [(index, rest) for name, index, rest in keys if name == "tags"]
    if not entries:
        return None, errors, warnings, has_aliases
    if len(entries) > 1:
        errors.append("tags 字段重复")
        return None, errors, warnings, has_aliases
    index, rest = entries[0]
    try:
        return parse_tags(rest, lines[index + 1:]), errors, warnings, has_aliases
    except TagFormatError as error:
        errors.append(str(error))
        return None, errors, warnings, has_aliases


def markdown_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(
        path for path in root.rglob("*.md")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def audit(root: Path, profile: dict, strict_cross_namespace: bool) -> tuple[dict, bool]:
    base = root if root.is_dir() else root.parent
    policy = profile["tags"]
    strategy = policy["strategy"]
    allowed = set(policy["allowed_namespaces"])
    enforce_namespaces = strategy in {"recommended", "custom"} and bool(allowed)
    errors: list[str] = []
    warnings: list[str] = []
    all_tags: list[str] = []
    files_with_tags = 0
    alias_fields = 0
    leaf_uses: dict[str, list[dict[str, str]]] = defaultdict(list)
    files = markdown_files(root)

    for path in files:
        name = path.relative_to(base).as_posix()
        lines, problem = read_frontmatter(path)
        if lines is None:
            if problem:
                errors.append(f"{name}：{problem}")
            else:
                warnings.append(f"{name}：没有 frontmatter")
            continue
        tags, file_errors, file_warnings, has_aliases = audit_file(lines)
        errors.extend(f"{name}：{message}" for message in file_errors)
        warnings.extend(f"{name}：{message}" for message in file_warnings)
        if has_aliases:
            alias_fields += 1
            if policy["reject_aliases"]:
                errors.append(f"{name}：profile 禁止使用 aliases")
        if tags is None:
            if not file_errors:
                warnings.append(f"{name}：没有 tags 字段")
            continue
        files_with_tags += 1
        duplicates = sorted({tag for tag in tags if tags.count(tag) > 1})
        if duplicates:
            errors.append(f"{name}：重复标签：{', '.join(duplicates)}")
        for tag in tags:
            all_tags.append(tag)
            if "/" not in tag:
                if enforce_namespaces:
                    errors.append(f"{name}：标签缺少命名空间：{tag}")
                continue
            namespace, leaf = tag.split("/", 1)
            if enforce_namespaces and namespace not in allowed:
                errors.append(f"{name}：命名空间不在 profile 允许范围内：{tag}")
            if not leaf:
                errors.append(f"{name}：标签缺少叶节点：{tag}")
                continue
            leaf_uses[leaf].append({"namespace": namespace, "file": name, "tag": tag})

    cross_namespace = {
        leaf: uses for leaf, uses in sorted(leaf_uses.items())
        if len({use["namespace"] for use in uses}) > 1
    }
    if cross_namespace:
        message = "同一叶标签出现在多个命名空间：" + ", ".join(cross_namespace)
        if strict_cross_namespace and enforce_namespaces:
            errors.append(message)
        else:
            warnings.append(message)
    result = {
        "root": str(root), "tag_strategy": strategy, "markdown_files": len(files),
        "files_with_tags": files_with_tags, "alias_fields": alias_fields,
        "tag_occurrences": len(all_tags), "unique_tags": len(set(all_tags)),
        "cross_namespace_leafs": cross_namespace, "errors": errors, "warnings": warnings,
    }
    return result, not errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Markdown 文件或目录")
    parser.add_argument("--profile", type=Path, help=f"profile 文件；默认向上查找最近的 {PROFILE_NAME}")
    parser.add_argument("--strict-cross-namespace", action="store_true", help="把跨命名空间同叶标签视为错误")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.path.expanduser().resolve()
    if not root.exists():
        parser.error(f"路径不存在：{root}")
    profile_path = args.profile.expanduser().resolve() if args.profile else find_profile(root)
    if profile_path is None:
        parser.error(f"未找到 {PROFILE_NAME}；请用 --profile 指定")
    try:
        profile = load_profile(profile_path)
    except ProfileError as error:
        parser.error(str(error))
    profile_errors = validate_profile(profile)
    if profile_errors:
        parser.error("profile 无效：" + "；".join(profile_errors))
    result, passed = audit(root, profile, args.strict_cross_namespace)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for key in ("tag_strategy", "markdown_files", "files_with_tags", "alias_fields", "tag_occurrences", "unique_tags"):
            print(f"{key}={result[key]}")
        for warning in result["warnings"]:
            print(f"警告：{warning}")
        for error in result["errors"]:
            print(f"错误：{error}")
        print("结果：通过" if passed else "结果：未通过")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
