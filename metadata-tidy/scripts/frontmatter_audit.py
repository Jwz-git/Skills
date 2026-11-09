#!/usr/bin/env python3
"""审计 Markdown 笔记的 YAML frontmatter，并用快照证明批量修改只触及 frontmatter。

只解析 frontmatter 常用的 YAML 子集：顶层映射、普通或引号键、块列表（任意缩进，含零缩进 `- a`）、
单行或跨行内联列表、普通与引号标量、注释。块标量（`|`、`>`）和嵌套映射只确定范围，不扫描其中的键。
超出子集的写法（`?` 复杂键、顶层内联集合、YAML 指令、带锚点或类型标签的键）标为 unsupported，不做猜测。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SNAPSHOT_VERSION = 1
LIST_PROPERTIES = ("tags", "aliases", "cssclasses")
LEGACY_KEYS = {"tag": "tags", "alias": "aliases", "cssclass": "cssclasses"}
LIST_KINDS = ("block", "flow", "empty")
NULL_PLAINS = {"", "~", "null", "Null", "NULL"}
PREAMBLE = "#preamble"
LINE_BREAK = re.compile(r"\r\n|\r|\n")
BLOCK_SCALAR = re.compile(r"[|>][0-9+-]*$")
MAPPING_INDICATOR = re.compile(r":(?:[ \t]|$)")
NEWLINES = {"\n": "lf", "\r\n": "crlf", "\r": "cr"}
ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f",
    "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
    "L": " ", "P": " ",
}
HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}
HEX_DIGITS = set("0123456789abcdefABCDEF")

Line = Tuple[int, str]


class UsageError(Exception):
    pass


class QuoteError(Exception):
    pass


class FlowError(Exception):
    pass


class ParseStop(Exception):
    def __init__(self, status: str, code: str, message: str, line: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.line = line


def make_issue(code: str, severity: str, message: str, line: Optional[int] = None, **extra: Any) -> Dict[str, Any]:
    issue: Dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if line is not None:
        issue["line"] = line
    issue.update(extra)
    return issue


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_lines(text: str) -> List[Tuple[str, str]]:
    lines: List[Tuple[str, str]] = []
    start = 0
    for match in LINE_BREAK.finditer(text):
        lines.append((text[start:match.start()], match.group()))
        start = match.end()
    if start < len(text):
        lines.append((text[start:], ""))
    return lines


def newline_style(lines: List[Tuple[str, str]]) -> str:
    endings = {ending for _, ending in lines if ending}
    if not endings:
        return "none"
    if len(endings) > 1:
        return "mixed"
    return NEWLINES[endings.pop()]


def is_blank(text: str) -> bool:
    return not text.strip(" \t")


def is_comment(text: str) -> bool:
    return text.lstrip(" \t").startswith("#")


def is_dash_item(text: str) -> bool:
    return text == "-" or text.startswith("- ") or text.startswith("-\t")


def space_indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def strip_comment(text: str) -> str:
    for index, char in enumerate(text):
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            return text[:index]
    return text


def fold_tag(value: str) -> str:
    return unicodedata.normalize("NFC", value).lower()


def read_quoted(text: str, start: int) -> Tuple[Optional[str], int]:
    """Return (value, index after the closing quote); value is None for multi-line scalars, index -1 if unterminated."""
    quote = text[start]
    out: List[str] = []
    multiline = False
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\n":
            multiline = True
        elif quote == "'":
            if char == "'":
                if text.startswith("'", index + 1):
                    out.append("'")
                    index += 2
                    continue
                return (None if multiline else "".join(out)), index + 1
        elif char == "\\":
            code = text[index + 1:index + 2]
            if code in ESCAPES:
                out.append(ESCAPES[code])
                index += 2
                continue
            if code in HEX_ESCAPES:
                width = HEX_ESCAPES[code]
                digits = text[index + 2:index + 2 + width]
                point = int(digits, 16) if len(digits) == width and set(digits) <= HEX_DIGITS else -1
                if point < 0 or point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
                    raise QuoteError(f"双引号字符串含无效转义 \\{code}{digits}")
                out.append(chr(point))
                index += 2 + width
                continue
            if code == "\n":
                multiline = True
                index += 2
                continue
            raise QuoteError(f"双引号字符串含无效转义 \\{code}")
        elif char == '"':
            return (None if multiline else "".join(out)), index + 1
        out.append(char)
        index += 1
    return None, -1


def scan_flow(text: str) -> Tuple[Optional[int], List[Dict[str, Any]], bool]:
    """Scan the flow collection at text[0]; return (end, items, is_complex), end is None if unterminated."""
    closing = {"[": "]", "{": "}"}
    stack: List[str] = []
    items: List[Dict[str, Any]] = []
    buf: List[str] = []
    quoted: List[Any] = [False, None]
    is_complex = text[0] != "["
    last = ""

    def collecting() -> bool:
        return len(stack) == 1 and not is_complex

    def finish(final: bool) -> None:
        nonlocal is_complex
        plain = " ".join(part.strip(" \t") for part in "".join(buf).split("\n") if part.strip(" \t"))
        if quoted[0]:
            items.append({"kind": "scalar" if quoted[1] is not None else "complex", "value": quoted[1], "quoted": True})
        elif plain:
            if MAPPING_INDICATOR.search(plain):
                is_complex = True
            elif plain in NULL_PLAINS:
                items.append({"kind": "null", "value": None})
            else:
                items.append({"kind": "scalar", "value": plain, "quoted": False})
        elif not final:
            raise FlowError("内联列表中有空项")
        buf.clear()
        quoted[:] = [False, None]

    index = 0
    while index < len(text):
        char = text[index]
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        if char in "\"'" and last in ("", "[", "{", ",", ":", "?"):
            try:
                value, end = read_quoted(text, index)
            except QuoteError as exc:
                raise FlowError(str(exc)) from None
            if end < 0:
                return None, [], is_complex
            if collecting():
                if quoted[0] or "".join(buf).strip(" \t\n"):
                    raise FlowError("内联列表项格式错误")
                quoted[:] = [True, value]
            index = end
            last = char
            continue
        if char in "[{":
            stack.append(char)
            if len(stack) > 1:
                is_complex = True
        elif char in "]}":
            if not stack or closing[stack.pop()] != char:
                raise FlowError("内联集合的括号不匹配")
            if not stack:
                if not is_complex:
                    finish(final=True)
                return index + 1, items, is_complex
        elif char == "," and len(stack) == 1:
            if not is_complex:
                finish(final=False)
        elif collecting():
            if quoted[0] and not char.isspace():
                raise FlowError("引号字符串后还有多余内容")
            buf.append(char)
        if not char.isspace():
            last = char
        index += 1
    return None, [], is_complex


def check_plain(plain: str, line: int) -> None:
    if plain[0] in "%@`|>" or plain in ("-", "?", ":") or plain.startswith(("- ", "? ", ": ")):
        raise ParseStop("invalid", "invalid_plain_scalar", f"未加引号的值不能以 `{plain[0]}` 开头", line)
    if MAPPING_INDICATOR.search(plain):
        raise ParseStop("invalid", "invalid_plain_scalar", "未加引号的值中含 `: `，YAML 会解析失败，需要加引号", line)


def parse_key(text: str, line: int) -> Tuple[str, str]:
    first = text[0]
    if first in "\"'":
        try:
            key, end = read_quoted(text, 0)
        except QuoteError as exc:
            raise ParseStop("invalid", "invalid_escape", str(exc), line) from None
        if end < 0 or key is None:
            raise ParseStop("invalid", "unterminated_quote", "引号键没有在同一行闭合", line)
        rest = text[end:].lstrip(" \t")
        if rest.startswith(":") and (len(rest) == 1 or rest[1] in " \t"):
            return key, rest[1:]
        raise ParseStop("invalid", "invalid_line", "无法识别为“键: 值”", line)
    if first == "?" and (len(text) == 1 or text[1] in " \t"):
        raise ParseStop("unsupported", "complex_key", "使用了 `?` 复杂键", line)
    if is_dash_item(text):
        raise ParseStop("invalid", "not_mapping", "frontmatter 顶层不是键值映射", line)
    if first in "[{":
        raise ParseStop("unsupported", "flow_root", "frontmatter 顶层是内联集合", line)
    if first in "&!*":
        raise ParseStop("unsupported", "decorated_key", "键带有锚点、别名或类型标签", line)
    if first == "%":
        raise ParseStop("unsupported", "directive", "frontmatter 中出现 YAML 指令", line)
    if first not in "|>@`,]}":
        for index, char in enumerate(text):
            if char == "#" and index > 0 and text[index - 1] in " \t":
                break
            if char == ":" and (index + 1 == len(text) or text[index + 1] in " \t"):
                key = text[:index].rstrip(" \t")
                if key:
                    return key, text[index + 1:]
                break
    raise ParseStop("invalid", "invalid_line", "无法识别为“键: 值”", line)


def take_lines(fm: List[Line], start: int, allow_dash: bool, block_scalar: bool = False) -> int:
    index = start
    while index < len(fm):
        line, text = fm[index]
        if is_blank(text):
            index += 1
            continue
        if text[0] == " " or (text[0] == "\t" and not block_scalar):
            leading = text[:len(text) - len(text.lstrip(" \t"))]
            if not block_scalar and "\t" in leading:
                raise ParseStop("invalid", "tab_indentation", "缩进中含 Tab，YAML 不允许用 Tab 缩进", line)
            index += 1
            continue
        if is_comment(text) or (allow_dash and is_dash_item(text)):
            index += 1
            continue
        break
    return index


def parse_item(rest: str, line: int) -> Dict[str, Any]:
    item: Dict[str, Any] = {"line": line, "kind": "scalar", "value": None}
    stripped = rest.lstrip(" \t")
    if not stripped or stripped.startswith("#"):
        item["kind"] = "null"
        item["comment_only"] = bool(stripped)
        return item
    if stripped[0] in "\"'":
        try:
            value, end = read_quoted(stripped, 0)
        except QuoteError as exc:
            raise ParseStop("invalid", "invalid_escape", str(exc), line) from None
        if end < 0:
            raise ParseStop("unsupported", "multiline_item", "列表项使用了跨行引号字符串", line)
        if strip_comment(stripped[end:]).strip(" \t"):
            raise ParseStop("invalid", "invalid_line", "引号字符串后还有多余内容", line)
        item["value"] = value
        item["quoted"] = True
        return item
    if stripped[0] in "[{&*!|>" or is_dash_item(stripped):
        item["kind"] = "complex"
        return item
    plain = strip_comment(stripped).strip(" \t")
    if MAPPING_INDICATOR.search(plain):
        item["kind"] = "complex"
    elif plain in NULL_PLAINS:
        item["kind"] = "null"
    else:
        check_plain(plain, line)
        item["value"] = plain
    return item


def classify_block(entry: Dict[str, Any], cont: List[Line]) -> None:
    significant = [(line, text) for line, text in cont if not is_blank(text) and not is_comment(text)]
    if not significant:
        entry["kind"] = "empty"
        return
    first_line, first = significant[0]
    indent = space_indent(first)
    if is_dash_item(first[indent:]):
        items: List[Dict[str, Any]] = []
        for line, text in significant:
            current = space_indent(text)
            if current == indent and is_dash_item(text[current:]):
                items.append(parse_item(text[current + 1:], line))
            elif current > indent and items:
                items[-1].update(kind="complex", value=None)
            else:
                raise ParseStop("invalid", "bad_indentation", "列表项缩进不一致", line)
        entry.update(kind="block", indent=indent, items=items)
        return
    if any(not text.startswith(" ") for _, text in significant):
        raise ParseStop("invalid", "bad_indentation", "块内容缩进不一致", first_line)
    pieces = [strip_comment(text).strip(" \t") for _, text in significant]
    head = pieces[0]
    if head[0] in "\"'":
        try:
            value, end = read_quoted(head, 0)
        except QuoteError as exc:
            raise ParseStop("invalid", "invalid_escape", str(exc), first_line) from None
        if end > 0 and head[end:].lstrip(" \t").startswith(":"):
            entry["kind"] = "mapping"
        else:
            entry.update(kind="complex" if end < 0 or len(pieces) > 1 else "scalar", value=value)
        return
    if MAPPING_INDICATOR.search(head):
        entry["kind"] = "mapping"
        return
    for (line, _), piece in zip(significant, pieces):
        if is_dash_item(piece) or MAPPING_INDICATOR.search(piece):
            raise ParseStop("invalid", "invalid_line", "普通标量的续行里出现了列表项或 `: `", line)
    entry.update(kind="scalar", value=pieces[0] if len(pieces) == 1 else None)


def parse_flow_value(entry: Dict[str, Any], fm: List[Line], start: int, value: str) -> int:
    text = value
    end = start + 1
    while True:
        try:
            stop, items, is_complex = scan_flow(text)
        except FlowError as exc:
            raise ParseStop("invalid", "invalid_flow", str(exc), entry["line"]) from None
        if stop is not None:
            break
        if end >= len(fm) or fm[end][1].startswith("\t"):
            raise ParseStop("invalid", "unterminated_flow", "内联集合的括号没有闭合", entry["line"])
        text += "\n" + fm[end][1]
        end += 1
    if strip_comment(text[stop:]).strip(" \t\n"):
        raise ParseStop("invalid", "invalid_flow", "内联集合后还有多余内容", entry["line"])
    if value[0] == "{":
        entry["kind"] = "mapping"
    elif is_complex:
        entry["kind"] = "complex"
    else:
        entry.update(kind="flow", items=items)
    return end


def parse_quoted_value(entry: Dict[str, Any], fm: List[Line], start: int, value: str) -> int:
    text = value
    end = start + 1
    while True:
        try:
            decoded, stop = read_quoted(text, 0)
        except QuoteError as exc:
            raise ParseStop("invalid", "invalid_escape", str(exc), entry["line"]) from None
        if stop >= 0:
            break
        if end >= len(fm) or not (is_blank(fm[end][1]) or fm[end][1].startswith(" ")):
            raise ParseStop("invalid", "unterminated_quote", "引号字符串没有闭合", entry["line"])
        text += "\n" + fm[end][1]
        end += 1
    if strip_comment(text[stop:]).strip(" \t"):
        raise ParseStop("invalid", "invalid_line", "引号字符串后还有多余内容", entry["line"])
    entry.update(kind="scalar", value=decoded, quoted=True)
    return end


def parse_plain_value(entry: Dict[str, Any], fm: List[Line], start: int, value: str) -> int:
    plain = strip_comment(value).strip(" \t")
    check_plain(plain, entry["line"])
    end = take_lines(fm, start + 1, allow_dash=False)
    pieces = [plain]
    for line, text in fm[start + 1:end]:
        if is_blank(text) or is_comment(text):
            continue
        piece = strip_comment(text).strip(" \t")
        if is_dash_item(piece) or MAPPING_INDICATOR.search(piece):
            raise ParseStop("invalid", "invalid_line", "普通标量的续行里出现了列表项或 `: `", line)
        pieces.append(piece)
    if len(pieces) == 1 and plain in NULL_PLAINS:
        entry["kind"] = "empty"
    else:
        entry.update(kind="scalar", value=plain if len(pieces) == 1 else None)
    return end


def parse_entry(fm: List[Line], start: int, key: str, rest: str) -> Tuple[Dict[str, Any], int]:
    entry: Dict[str, Any] = {"key": key, "line": fm[start][0], "kind": "scalar", "value": None}
    value = rest.strip(" \t")
    if not value or value.startswith("#"):
        end = take_lines(fm, start + 1, allow_dash=True)
        classify_block(entry, fm[start + 1:end])
    elif BLOCK_SCALAR.match(strip_comment(value).rstrip(" \t")):
        end = take_lines(fm, start + 1, allow_dash=False, block_scalar=True)
        entry["kind"] = "block_scalar"
    elif value[0] in "[{":
        end = parse_flow_value(entry, fm, start, value)
    elif value[0] in "\"'":
        end = parse_quoted_value(entry, fm, start, value)
    elif value[0] in "&!*":
        end = take_lines(fm, start + 1, allow_dash=True)
        entry["kind"] = "complex"
    else:
        end = parse_plain_value(entry, fm, start, value)
    entry["lines"] = list(fm[start:end])
    return entry, end


def parse_frontmatter(fm: List[Line]) -> Tuple[List[Line], List[Dict[str, Any]]]:
    preamble: List[Line] = []
    entries: List[Dict[str, Any]] = []
    index = 0
    while index < len(fm):
        line, text = fm[index]
        if is_blank(text) or is_comment(text):
            (entries[-1]["lines"] if entries else preamble).append((line, text))
            index += 1
            continue
        if text[0] == "\t":
            raise ParseStop("invalid", "tab_indentation", "缩进中含 Tab，YAML 不允许用 Tab 缩进", line)
        if text[0] == " ":
            raise ParseStop("invalid", "bad_indentation", "出现无法归属到任何键的缩进行", line)
        key, rest = parse_key(text, line)
        entry, index = parse_entry(fm, index, key, rest)
        entries.append(entry)
    return preamble, entries


def describe_value(entry: Dict[str, Any]) -> Dict[str, Any]:
    described: Dict[str, Any] = {"kind": entry["kind"], "line": entry["line"]}
    if entry["kind"] == "block":
        described["indent"] = entry["indent"]
    if entry["kind"] in ("block", "flow"):
        described["values"] = [item["value"] if item["kind"] == "scalar" else None for item in entry["items"]]
    elif entry["kind"] == "scalar":
        described["value"] = entry.get("value")
    return described


def check_tag(value: str, line: int) -> List[Dict[str, Any]]:
    issues = []
    if any(char.isspace() for char in value):
        issues.append(make_issue("tag_whitespace", "warning", f"tag `{value}` 含空白；Obsidian tag 不能含空格", line, key="tags", value=value))
    if re.fullmatch(r"[0-9]+", value):
        issues.append(make_issue("tag_numeric", "warning", f"tag `{value}` 全是数字；Obsidian tag 至少需要一个非数字字符", line, key="tags", value=value))
    unlisted = sorted({char for char in value if char.isascii() and not char.isalnum() and not char.isspace() and char not in "_-/"})
    if unlisted:
        chars = " ".join(unlisted)
        issues.append(make_issue("tag_unlisted_chars", "warning", f"tag `{value}` 含 Obsidian 帮助文档未列出的字符 {chars}，是否被识别需在目标版本中确认", line, key="tags", value=value))
    return issues


def check_list_property(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = entry["key"]
    kind = entry["kind"]
    line = entry["line"]
    if kind == "scalar":
        return [make_issue("scalar_list_property", "warning", f"`{key}` 是文本值而不是列表；Obsidian 1.9 起不再识别这类文本值", line, key=key)]
    if kind == "empty":
        return [make_issue("empty_property", "info", f"`{key}` 没有值", line, key=key)]
    if kind not in ("block", "flow"):
        return [make_issue("unsupported_value", "warning", f"`{key}` 的值是 {kind} 结构，脚本不做判断", line, key=key)]
    issues: List[Dict[str, Any]] = []
    exact: Dict[str, int] = {}
    folded: Dict[str, str] = {}
    for item in entry["items"]:
        item_line = item.get("line", line)
        if item["kind"] == "null" or (item["kind"] == "scalar" and not item["value"].strip()):
            message = f"`{key}` 有空列表项"
            if item.get("comment_only"):
                message += "；未加引号的 `#...` 会被 YAML 当作注释"
            issues.append(make_issue("empty_item", "warning", message, item_line, key=key))
            continue
        if item["kind"] == "complex":
            issues.append(make_issue("unsupported_item", "warning", f"`{key}` 有嵌套或跨行的列表项，脚本不做判断", item_line, key=key))
            continue
        value = item["value"]
        if value in exact:
            issues.append(make_issue("duplicate_item", "warning", f"`{key}` 中 `{value}` 重复出现", item_line, key=key, value=value))
        elif key == "tags" and fold_tag(value) in folded:
            first = folded[fold_tag(value)]
            issues.append(make_issue("case_duplicate_item", "warning", f"tag `{value}` 与 `{first}` 只有大小写或 Unicode 形式不同，Obsidian 视为同一个 tag", item_line, key=key, value=value))
        exact.setdefault(value, item_line)
        folded.setdefault(fold_tag(value), value)
        if key == "tags":
            issues.extend(check_tag(value, item_line))
    return issues


def check_keys(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lines: Dict[str, List[int]] = defaultdict(list)
    for entry in entries:
        lines[entry["key"]].append(entry["line"])
    spellings: Dict[str, List[str]] = defaultdict(list)
    for key in lines:
        spellings[key.lower()].append(key)
    for key, found in lines.items():
        if len(found) > 1:
            issues.append(make_issue("duplicate_key", "error", f"顶层键 `{key}` 重复出现；不同解析器取值不同，不能自动处理", found[0], key=key, lines=found))
        lowered = key.lower()
        if len(spellings[lowered]) > 1 and spellings[lowered][0] == key:
            names = "、".join(f"`{name}`" for name in spellings[lowered])
            issues.append(make_issue("case_variant_keys", "warning", f"键名只有大小写不同：{names}", found[0], key=key))
        if lowered in LEGACY_KEYS:
            new = LEGACY_KEYS[lowered]
            issues.append(make_issue("legacy_key", "warning", f"`{key}` 是旧属性名：Obsidian 1.4 起弃用，1.9 起不再识别；迁移为 `{new}` 需要授权", found[0], key=key))
            if new in lines:
                issues.append(make_issue("legacy_collision", "warning", f"`{key}` 与 `{new}` 同时存在，迁移前需决定合并规则", found[0], key=key))
        elif lowered in LIST_PROPERTIES and key != lowered:
            issues.append(make_issue("noncanonical_key_case", "info", f"键名 `{key}` 与 Obsidian 默认属性名 `{lowered}` 大小写不同", found[0], key=key))
    return issues


def analyze_bytes(data: bytes, allow_dot_end: bool = False, fields: Tuple[str, ...] = ()) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "status": "ok", "editable": False, "bom": data.startswith(b"\xef\xbb\xbf"), "newline": "none",
        "frontmatter_lines": None, "keys": [], "properties": {}, "issues": [],
        "file_sha256": hashlib.sha256(data).hexdigest(), "frontmatter_sha256": None, "body_sha256": None,
        "entries": [],
    }
    issues: List[Dict[str, Any]] = record["issues"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        record["status"] = "decode_error"
        issues.append(make_issue("decode_error", "error", f"不是有效的 UTF-8：{exc.reason}（字节 {exc.start}）"))
        return record
    if record["bom"]:
        text = text[1:]
    lines = split_lines(text)
    record["newline"] = newline_style(lines)
    if record["newline"] == "mixed":
        issues.append(make_issue("mixed_newlines", "info", "文件混用多种换行符"))
    first = lines[0][0] if lines else ""
    if first != "---":
        record["status"] = "none"
        record["body_sha256"] = text_sha256(text)
        if first.rstrip(" \t") == "---":
            issues.append(make_issue("delimiter_whitespace", "warning", "第 1 行的 `---` 带尾随空白，不被视为 frontmatter 开始标记", 1))
        elif first == "+++":
            record["status"] = "unsupported"
            issues.append(make_issue("toml_frontmatter", "info", "TOML frontmatter（`+++`）不在本脚本支持范围内", 1))
        else:
            issues.append(make_issue("no_frontmatter", "info", "没有 frontmatter"))
        return record
    close: Optional[int] = None
    dot: Optional[int] = None
    for index in range(1, len(lines)):
        content = lines[index][0]
        if content == "---":
            close = index
            break
        if content == "...":
            dot = index if dot is None else dot
        elif content.rstrip(" \t") in ("---", "..."):
            issues.append(make_issue("delimiter_whitespace", "warning", f"第 {index + 1} 行的边界标记带尾随空白，不被视为结束标记", index + 1))
    if dot is not None and (close is None or dot < close):
        if allow_dot_end:
            close = dot
            issues.append(make_issue("dot_end_accepted", "info", "按 --allow-dot-end 把 `...` 当作结束标记", dot + 1))
        elif close is None:
            issues.append(make_issue("dot_end_marker", "warning", "`...` 不是 Obsidian frontmatter 的结束标记", dot + 1))
        else:
            record["status"] = "invalid"
            issues.append(make_issue("dot_end_marker", "error", "`...` 出现在结束标记 `---` 之前，YAML 会在此结束文档", dot + 1))
    if close is None:
        record["status"] = "unclosed"
        record["frontmatter_lines"] = [1, None]
        issues.append(make_issue("unclosed", "error", "有开始标记 `---` 但没有结束标记，不能把正文当作 YAML", 1))
        return record
    head = "".join(content + ending for content, ending in lines[:close + 1])
    record["frontmatter_lines"] = [1, close + 1]
    record["frontmatter_sha256"] = text_sha256(head)
    record["body_sha256"] = text_sha256(text[len(head):])
    if record["status"] != "ok":
        return record
    fm = [(index + 1, lines[index][0]) for index in range(1, close)]
    try:
        preamble, entries = parse_frontmatter(fm)
    except ParseStop as stop:
        record["status"] = stop.status
        issues.append(make_issue(stop.code, "error", stop.message, stop.line))
        return record
    issues.extend(check_keys(entries))
    duplicated = {key for key, count in Counter(entry["key"] for entry in entries).items() if count > 1}
    wanted = set(LIST_PROPERTIES) | set(fields) | {"title"}
    for entry in entries:
        item: Dict[str, Any] = {"key": entry["key"], "line": entry["line"], "kind": entry["kind"]}
        record["keys"].append(item)
        key = entry["key"]
        if key in duplicated:
            continue
        if key in wanted or key.lower() in LEGACY_KEYS:
            record["properties"][key] = describe_value(entry)
        if key in LIST_PROPERTIES:
            issues.extend(check_list_property(entry))
    if preamble:
        record["entries"].append([PREAMBLE, text_sha256("\n".join(text for _, text in preamble))])
    for entry in entries:
        record["entries"].append([entry["key"], text_sha256("\n".join(text for _, text in entry["lines"]))])
    record["editable"] = not any(issue["severity"] == "error" for issue in issues)
    return record


def compile_glob(pattern: str) -> Tuple["re.Pattern[str]", bool]:
    parts: List[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        elif char == "[" and "]" in pattern[index + 2:]:
            end = pattern.index("]", index + 2)
            body = pattern[index + 1:end]
            parts.append("[" + ("^" + body[1:] if body.startswith("!") else body).replace("\\", "\\\\") + "]")
            index = end + 1
            continue
        else:
            parts.append(re.escape(char))
        index += 1
    return re.compile("".join(parts) + r"\Z"), "/" not in pattern


def matches(patterns: List[Tuple["re.Pattern[str]", bool]], rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(regex.match(name if basename else rel) for regex, basename in patterns)


def discover(root: Path, options: Dict[str, Any]) -> Dict[str, Any]:
    if not root.exists():
        raise UsageError(f"路径不存在：{root}")
    if root.is_file():
        return {"markdown": [(root.name, root)], "others": [], "skipped": {"hidden": 0, "excluded": 0, "symlinks": []}}
    includes = [compile_glob(pattern) for pattern in options["include"]]
    excludes = [compile_glob(pattern) for pattern in options["exclude"]]
    markdown: List[Tuple[str, Path]] = []
    others: List[str] = []
    skipped: Dict[str, Any] = {"hidden": 0, "excluded": 0, "symlinks": []}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        prefix = base.relative_to(root).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        kept = []
        for name in sorted(dirnames):
            if name.startswith(".") and not options["include_hidden"]:
                skipped["hidden"] += 1
            elif (base / name).is_symlink():
                skipped["symlinks"].append(prefix + name)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            rel = prefix + name
            if name.startswith(".") and not options["include_hidden"]:
                skipped["hidden"] += 1
            elif (base / name).is_symlink():
                skipped["symlinks"].append(rel)
            elif matches(excludes, rel):
                skipped["excluded"] += 1
            elif name.lower().endswith(".md") and (not includes or matches(includes, rel)):
                markdown.append((rel, base / name))
            else:
                others.append(rel)
    return {"markdown": sorted(markdown), "others": sorted(others), "skipped": skipped}


def collect(root: Path, options: Dict[str, Any], fields: Tuple[str, ...] = ()) -> Dict[str, Any]:
    found = discover(root, options)
    records = {}
    for rel, path in found["markdown"]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise UsageError(f"无法读取 {path}：{exc}") from exc
        records[rel] = analyze_bytes(data, options["allow_dot_end"], fields)
    return {"files": records, "others": found["others"], "skipped": found["skipped"]}


def value_inventory(records: Dict[str, Dict[str, Any]], field: str, include_scalar: bool) -> Dict[str, Any]:
    kinds: Counter = Counter()
    occurrences: Counter = Counter()
    files: Dict[str, set] = defaultdict(set)
    for rel, record in records.items():
        prop = record["properties"].get(field)
        if prop is None:
            continue
        kinds[prop["kind"]] += 1
        values = prop.get("values", [])
        if include_scalar and prop["kind"] == "scalar":
            values = [prop.get("value")]
        for value in values:
            if value is not None:
                occurrences[value] += 1
                files[value].add(rel)
    rows = [{"value": value, "occurrences": count, "files": len(files[value])} for value, count in occurrences.items()]
    rows.sort(key=lambda row: (-row["occurrences"], row["value"]))
    return {"kinds": dict(sorted(kinds.items())), "values": rows}


def shared_values(records: Dict[str, Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    owners: Dict[str, set] = defaultdict(set)
    for rel, record in records.items():
        prop = record["properties"].get(field)
        if not prop:
            continue
        values = prop.get("values") or ([prop["value"]] if prop.get("value") is not None else [])
        for value in values:
            if value is not None:
                owners[value].add(rel)
    return [{"value": value, "files": sorted(paths)} for value, paths in sorted(owners.items()) if len(paths) > 1]


def audit(root: Path, options: Dict[str, Any], fields: Tuple[str, ...], issues_only: bool) -> Tuple[Dict[str, Any], int]:
    collected = collect(root, options, fields)
    records = collected["files"]
    status_counts = Counter(record["status"] for record in records.values())
    issue_counts = Counter(issue["code"] for record in records.values() for issue in record["issues"])
    flagged = {
        rel for rel, record in records.items()
        if record["status"] != "ok" or any(issue["severity"] != "info" for issue in record["issues"])
    }
    findings = any(
        record["status"] not in ("ok", "none") or any(issue["severity"] != "info" for issue in record["issues"])
        for record in records.values()
    )
    keys = Counter(key for record in records.values() for key in {item["key"] for item in record["keys"]})
    tag_groups: Dict[str, Counter] = defaultdict(Counter)
    for row in value_inventory(records, "tags", include_scalar=False)["values"]:
        tag_groups[fold_tag(row["value"])][row["value"]] += row["occurrences"]
    inventory = {
        "keys": dict(sorted(keys.items(), key=lambda pair: (-pair[1], pair[0]))),
        "fields": {
            field: value_inventory(records, field, include_scalar=field not in LIST_PROPERTIES)
            for field in list(LIST_PROPERTIES) + [name for name in fields if name not in LIST_PROPERTIES]
        },
        "tag_variants": [
            {"key": key, "spellings": dict(spellings)} for key, spellings in sorted(tag_groups.items()) if len(spellings) > 1
        ],
        "shared_aliases": shared_values(records, "aliases"),
        "shared_titles": shared_values(records, "title"),
    }
    files = []
    for rel, record in sorted(records.items()):
        if issues_only and rel not in flagged:
            continue
        files.append({"path": rel, **{key: record[key] for key in (
            "status", "editable", "bom", "newline", "frontmatter_lines", "keys", "properties", "issues")}})
    report = {
        "root": str(root),
        "options": options,
        "summary": {
            "files_scanned": len(records),
            "status_counts": dict(sorted(status_counts.items())),
            "editable": sum(1 for record in records.values() if record["editable"]),
            "files_with_findings": len(flagged),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "discovery": collected["skipped"],
        "inventory": inventory,
        "files": files,
    }
    return report, 1 if findings else 0


def snapshot_record(record: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("status", "editable", "bom", "newline", "file_sha256", "frontmatter_sha256", "body_sha256", "entries")
    result = {key: record[key] for key in keep}
    result["lists"] = {key: record["properties"][key] for key in LIST_PROPERTIES if key in record["properties"]}
    result["error_codes"] = sorted({issue["code"] for issue in record["issues"] if issue["severity"] == "error"})
    return result


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def snapshot(root: Path, options: Dict[str, Any], out: Path, force: bool) -> Dict[str, Any]:
    resolved_root = root.resolve()
    resolved_out = out.resolve()
    if inside(resolved_out, resolved_root):
        raise UsageError('快照不能写进扫描范围，请改用临时目录，例如 "$(mktemp -d)/before.json"')
    if not resolved_out.parent.is_dir():
        raise UsageError(f"快照目录不存在：{resolved_out.parent}")
    if resolved_out.exists() and not force:
        raise UsageError(f"快照文件已存在：{resolved_out}；确认可以覆盖时再加 --force")
    collected = collect(root, options)
    payload = {
        "version": SNAPSHOT_VERSION,
        "root": str(resolved_root),
        "options": options,
        "files": {rel: snapshot_record(record) for rel, record in collected["files"].items()},
        "other_files": collected["others"],
    }
    write_json(resolved_out, payload)
    return {"snapshot": str(resolved_out), "files": len(payload["files"]), "other_files": len(payload["other_files"])}


def duplicate_values(values: List[Optional[str]], fold: bool) -> set:
    counts = Counter(fold_tag(value) if fold else value for value in values if value is not None)
    return {value for value, count in counts.items() if count > 1}


def list_checks(key: str, before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    violations: List[Tuple[str, str]] = []
    warnings: List[Tuple[str, str]] = []
    if after is None:
        return violations, warnings
    if after["kind"] not in LIST_KINDS:
        violations.append(("list_property_not_list", f"`{key}` 修改后不是列表（{after['kind']}）"))
        return violations, warnings
    old = before.get("values", []) if before else []
    new = after.get("values", [])
    if new.count(None) > old.count(None):
        violations.append(("invalid_list_item", f"`{key}` 修改后多出空项或无法判定的项"))
    introduced = duplicate_values(new, fold=False) - duplicate_values(old, fold=False)
    if introduced:
        violations.append(("duplicate_items", f"`{key}` 修改后出现重复值：" + "、".join(sorted(introduced))))
    elif duplicate_values(new, fold=False):
        warnings.append(("duplicate_items_kept", f"`{key}` 保留了修改前已有的重复值"))
    if key == "tags" and duplicate_values(new, fold=True) - duplicate_values(new, fold=False):
        warnings.append(("case_duplicate_items", "`tags` 中有只差大小写的值，最终拼写需要用户决定"))
    if before and before["kind"] != after["kind"] and before["kind"] in ("block", "flow"):
        warnings.append(("list_style_changed", f"`{key}` 从 {before['kind']} 写法改成了 {after['kind']} 写法"))
    return violations, warnings


def verify(root: Path, snapshot_path: Path, fields: Tuple[str, ...]) -> Tuple[Dict[str, Any], int]:
    try:
        saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"无法读取快照 {snapshot_path}：{exc}") from exc
    if not isinstance(saved, dict) or saved.get("version") != SNAPSHOT_VERSION:
        raise UsageError("快照格式不受支持")
    if Path(saved["root"]) != root.resolve():
        raise UsageError(f"快照对应的路径是 {saved['root']}，与本次校验路径不同")
    current = collect(root, saved["options"])
    before: Dict[str, Dict[str, Any]] = saved["files"]
    after = {rel: snapshot_record(record) for rel, record in current["files"].items()}
    violations: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []

    def violate(rel: str, code: str, message: str) -> None:
        violations.append({"path": rel, "code": code, "message": message})

    for rel in sorted(set(after) - set(before)):
        violate(rel, "file_added", "出现了快照中没有的 Markdown 文件")
    for rel in sorted(set(before) - set(after)):
        violate(rel, "file_removed", "快照中的 Markdown 文件已不存在或已改名")
    for rel in sorted(set(current["others"]) - set(saved["other_files"])):
        violate(rel, "other_file_added", "扫描范围内新增了非 Markdown 文件")
    for rel in sorted(set(saved["other_files"]) - set(current["others"])):
        violate(rel, "other_file_removed", "扫描范围内的非 Markdown 文件已不存在或已改名")
    allowed = set(fields)
    unchanged = 0
    for rel in sorted(set(before) & set(after)):
        old, new = before[rel], after[rel]
        if old["file_sha256"] == new["file_sha256"]:
            unchanged += 1
            continue
        if old["bom"] != new["bom"]:
            violate(rel, "bom_changed", "UTF-8 BOM 被增加或删除")
        if old["newline"] not in ("none", new["newline"]):
            violate(rel, "newline_changed", f"换行风格从 {old['newline']} 变成了 {new['newline']}")
        if old["status"] == "none":
            if new["status"] == "none":
                violate(rel, "body_changed", "没有 frontmatter 的文件内容被修改")
                continue
            if not new["editable"]:
                violate(rel, "frontmatter_broken", f"新增的 frontmatter 无法通过检查（{new['status']}：{', '.join(new['error_codes'])}）")
                continue
            if new["body_sha256"] != old["body_sha256"]:
                violate(rel, "body_changed", "新增 frontmatter 时改动了原有内容；新 frontmatter 应直接插在文件开头，不额外插入空行")
            added = [key for key, _ in new["entries"]]
            touched = added
            change = {"path": rel, "change": "frontmatter_added", "keys_added": added}
        elif not old["editable"]:
            reason = old["status"] if old["status"] != "ok" else "、".join(old["error_codes"])
            violate(rel, "edited_unsafe_file", f"修改前该文件不可安全编辑（{reason}），无法证明修改安全；需用户明确授权并人工核对 diff")
            continue
        else:
            if not new["editable"]:
                violate(rel, "frontmatter_broken", f"修改后 frontmatter 无法通过检查（{new['status']}：{', '.join(new['error_codes'])}）")
                continue
            if new["body_sha256"] != old["body_sha256"]:
                violate(rel, "body_changed", "正文（结束标记之后的内容）发生了变化")
            old_map, new_map = dict(old["entries"]), dict(new["entries"])
            old_order = [key for key, _ in old["entries"]]
            new_order = [key for key, _ in new["entries"]]
            added = [key for key in new_order if key not in old_map]
            removed = [key for key in old_order if key not in new_map]
            modified = [key for key in old_order if key in new_map and new_map[key] != old_map[key]]
            if [key for key in old_order if key in new_map] != [key for key in new_order if key in old_map]:
                violate(rel, "key_order_changed", "改变了原有键的顺序")
            touched = added + removed + modified
            change = {"path": rel, "change": "frontmatter_changed", "keys_added": added, "keys_removed": removed, "keys_modified": modified}
        if allowed:
            outside = [key for key in touched if key not in allowed]
            if outside:
                violate(rel, "unauthorized_key_change", "改动了授权范围外的键：" + "、".join(outside))
        for key in added:
            if key.lower() in LEGACY_KEYS:
                violate(rel, "legacy_key_added", f"新增了旧属性名 `{key}`")
        lists = {}
        for key in LIST_PROPERTIES:
            if key in touched:
                old_prop, new_prop = old["lists"].get(key), new["lists"].get(key)
                lists[key] = {"before": old_prop, "after": new_prop}
                found_violations, found_warnings = list_checks(key, old_prop, new_prop)
                for code, message in found_violations:
                    violate(rel, code, message)
                for code, message in found_warnings:
                    warnings.append({"path": rel, "code": code, "message": message})
        if lists:
            change["lists"] = lists
        changed.append(change)
    report = {
        "ok": not violations,
        "summary": {
            "files_checked": len(set(before) | set(after)),
            "unchanged": unchanged,
            "changed": len(changed),
            "violations": len(violations),
            "warnings": len(warnings),
            "fields": sorted(allowed),
        },
        "changed": changed,
        "violations": violations,
        "warnings": warnings,
    }
    return report, 0 if not violations else 1


def emit(stream: Any, text: str) -> None:
    stream.buffer.write(text.encode("utf-8"))
    stream.buffer.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计 Markdown frontmatter，并用快照校验批量修改。")
    commands = parser.add_subparsers(dest="command", required=True)

    def add_scope(command: argparse.ArgumentParser) -> None:
        command.add_argument("path", type=Path, help="Markdown 文件或目录")
        command.add_argument("--include", action="append", default=[], metavar="GLOB", help="只处理匹配的 Markdown 文件，可重复")
        command.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="排除匹配的文件，可重复")
        command.add_argument("--include-hidden", action="store_true", help="同时扫描以 . 开头的文件和目录")
        command.add_argument("--allow-dot-end", action="store_true", help="把 `...` 当作结束标记（仅限明确支持它的方言）")

    audit_command = commands.add_parser("audit", help="只读盘点 frontmatter 结构、键和列表属性")
    add_scope(audit_command)
    audit_command.add_argument("--field", action="append", default=[], metavar="KEY", help="额外统计该字段的值，可重复")
    audit_command.add_argument("--issues-only", action="store_true", help="files 中只列出有问题的文件")
    snapshot_command = commands.add_parser("snapshot", help="记录修改前的正文与 frontmatter 哈希")
    add_scope(snapshot_command)
    snapshot_command.add_argument("--out", type=Path, required=True, help="快照 JSON 路径，必须在扫描范围外")
    snapshot_command.add_argument("--force", action="store_true", help="覆盖已有快照文件")
    verify_command = commands.add_parser("verify", help="对照快照检查正文、文件集合与授权字段")
    verify_command.add_argument("path", type=Path, help="与快照相同的文件或目录")
    verify_command.add_argument("--snapshot", type=Path, required=True)
    verify_command.add_argument("--fields", action="append", default=[], metavar="KEYS", help="允许改动的顶层键，逗号分隔或重复")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "verify":
            fields = tuple(name.strip() for value in args.fields for name in value.split(",") if name.strip())
            report, code = verify(args.path, args.snapshot, fields)
        else:
            options = {
                "include": args.include, "exclude": args.exclude,
                "include_hidden": args.include_hidden, "allow_dot_end": args.allow_dot_end,
            }
            if args.command == "audit":
                report, code = audit(args.path, options, tuple(args.field), args.issues_only)
            else:
                report, code = snapshot(args.path, options, args.out, args.force), 0
    except UsageError as exc:
        emit(sys.stderr, f"错误：{exc}\n")
        return 2
    emit(sys.stdout, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
