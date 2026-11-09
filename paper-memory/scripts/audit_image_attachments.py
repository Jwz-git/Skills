#!/usr/bin/env python3
"""只读审计本地论文笔记的图片附件，规则来自 paper-memory profile。

退出码：0 未发现需修复的问题；1 存在断链、歧义、位置不符、无法读取的笔记，
或与 attachments.link_style 不一致的 Wiki 嵌入；2 参数或 profile 错误。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import unquote

from profile_config import (
    PROFILE_NAME, ProfileError, expand_attachment_parent, find_profile, load_profile,
    local_memory_root, validate_profile,
)


IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?:[^\[\]\\\n]|\\.|\[[^\[\]\n]*\])*\]"
    r"\(\s*(<[^<>\n]*>|(?:[^()\s\\]|\\.|\([^()\s]*\)|[ \t])+?)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
WIKI_EMBED_RE = re.compile(r"!\[\[([^\[\]|#^\n]+)(?:[#^][^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def walk_files(base: Path) -> Iterator[Path]:
    for directory, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if not name.startswith("."):
                yield Path(directory) / name


def find_vault_root(memory_root: Path) -> Optional[Path]:
    for directory in (memory_root, *memory_root.parents):
        if (directory / ".obsidian").is_dir():
            return directory
    return None


def strip_code(text: str) -> str:
    kept: list[str] = []
    fence: Optional[str] = None
    for line in text.splitlines():
        if fence is None:
            match = FENCE_RE.match(line)
            fence = match.group(1) if match else None
            kept.append("" if match else line)
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if indent <= 3 and stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
            fence = None
        kept.append("")
    return INLINE_CODE_RE.sub(" ", "\n".join(kept))


def extract_references(text: str) -> list[tuple[str, str]]:
    text = strip_code(text)
    found = [("markdown", match.group(1)) for match in MARKDOWN_IMAGE_RE.finditer(text)]
    found += [("wiki", match.group(1).strip()) for match in WIKI_EMBED_RE.finditer(text)]
    found += [
        ("html", html.unescape(match.group(1) if match.group(1) is not None else match.group(2)))
        for match in HTML_IMAGE_RE.finditer(text)
    ]
    return found


def link_path(syntax: str, raw: str) -> Optional[str]:
    target = raw.strip()
    if syntax == "wiki":
        return target or None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith("//") or SCHEME_RE.match(target):
        return None
    target = re.split(r"[?#]", target, maxsplit=1)[0]
    target = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", target)
    return unquote(target) or None


class Resolver:
    def __init__(self, base: Path, obsidian: bool, images: list[Path]):
        self.base = base
        self.obsidian = obsidian
        self.exact = {nfc(str(image)): image for image in images}
        self.by_name: dict[str, list[Path]] = defaultdict(list)
        for image in images:
            self.by_name[nfc(image.name)].append(image)

    def check(self, path: Path) -> tuple[Optional[Path], bool]:
        path = Path(os.path.normpath(str(path)))
        if not path.is_file():
            return None, False
        image = self.exact.get(nfc(str(path)))
        if image is not None:
            return image, False
        if path.is_relative_to(self.base):
            hidden = any(part.startswith(".") for part in path.relative_to(self.base).parts)
            if not hidden:
                return None, True
        return path, False

    def resolve(self, note: Path, syntax: str, target: str) -> tuple[Optional[Path], list[Path], str]:
        vault_semantics = self.obsidian or syntax == "wiki"
        relative = target.startswith(("./", "../"))
        if target.startswith("/"):
            paths = [self.base / target.lstrip("/")] if vault_semantics else [Path(target)]
        else:
            paths = [note.parent / target]
            if "/" in target and not relative and vault_semantics:
                paths.append(self.base / target)
        case_mismatch = False
        for path in paths:
            image, mismatch = self.check(path)
            if image is not None:
                return image, [], ""
            case_mismatch = case_mismatch or mismatch
        if case_mismatch:
            return None, [], "路径大小写与实际文件不一致"
        if "/" not in target and vault_semantics:
            matches = self.by_name.get(nfc(target), [])
            if len(matches) == 1:
                return matches[0], [], ""
            if matches:
                return None, matches, ""
        return None, [], "未找到目标文件"


def audit(memory_root: Path, scopes: list[str], profile: dict) -> dict:
    obsidian = profile["memory"]["provider"] == "obsidian"
    vault_root = find_vault_root(memory_root) if obsidian else None
    base = vault_root or memory_root
    scope_roots = [(memory_root / scope).resolve() for scope in scopes] or [memory_root]
    files = list(walk_files(base))
    notes = [path for path in files if path.suffix.lower() == ".md"]
    images = [path for path in files if path.suffix.lower() in IMAGE_EXTENSIONS]
    resolver = Resolver(base, obsidian, images)

    def display(path: Path) -> str:
        return path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)

    def in_scope(path: Path) -> bool:
        return any(path.is_relative_to(scope) for scope in scope_roots)

    references: dict[Path, set[Path]] = defaultdict(set)
    broken: list[dict[str, str]] = []
    ambiguous: list[dict[str, object]] = []
    wiki_embeds: list[dict[str, str]] = []
    unreadable: list[str] = []
    scanned = 0
    reference_count = 0
    for note in notes:
        reported = in_scope(note)
        try:
            text = note.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            if reported:
                unreadable.append(display(note))
            continue
        scanned += int(reported)
        for syntax, raw in extract_references(text):
            target = link_path(syntax, raw)
            if not target or Path(target).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            image, candidates, reason = resolver.resolve(note, syntax, target)
            if image is not None:
                references[image].add(note)
            if not reported:
                continue
            reference_count += 1
            if syntax == "wiki":
                wiki_embeds.append({"note": display(note), "target": raw})
            if image is not None:
                continue
            entry = {"note": display(note), "target": raw, "syntax": syntax}
            if candidates:
                ambiguous.append({**entry, "candidates": [display(path) for path in candidates]})
            else:
                broken.append({**entry, "reason": reason})

    pattern = profile["attachments"].get("root_pattern")
    shared: list[dict[str, object]] = []
    misplaced: list[dict[str, str]] = []
    for image, referencing in sorted(references.items()):
        if len(referencing) > 1:
            if in_scope(image) or any(in_scope(note) for note in referencing):
                shared.append({"image": display(image), "notes": sorted(display(note) for note in referencing)})
            continue
        note = next(iter(referencing))
        if pattern and in_scope(note):
            expected = expand_attachment_parent(pattern, note)
            if image.parent != expected:
                misplaced.append({"image": display(image), "note": display(note), "expected_parent": display(expected)})

    scoped_images = [image for image in images if in_scope(image)]
    referenced_from_scope = {image for image, referencing in references.items() if any(in_scope(note) for note in referencing)}
    return {
        "memory_root": str(memory_root), "vault_root": str(vault_root) if vault_root else None,
        "paths_relative_to": str(base), "scopes": scopes,
        "notes_scanned": scanned, "notes_indexed": len(notes),
        "notes_with_images": len({note for referencing in references.values() for note in referencing if in_scope(note)}),
        "image_files": len(scoped_images), "image_references": reference_count,
        "unique_referenced_images": len(referenced_from_scope),
        "broken_references": broken, "ambiguous_references": ambiguous, "unreadable_notes": unreadable,
        "wiki_embeds": wiki_embeds, "shared_images": shared, "misplaced_images": misplaced,
        "unreferenced_images": [display(image) for image in scoped_images if image not in references],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Memory 根目录或其中的路径，用于查找 profile")
    parser.add_argument("--profile", type=Path, help=f"profile 文件；默认向上查找最近的 {PROFILE_NAME}")
    parser.add_argument("--scope", action="append", default=[], help="只报告 Memory 根目录下的该子目录，可重复")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    start = args.path.expanduser().resolve()
    profile_path = args.profile.expanduser().resolve() if args.profile else find_profile(start)
    if profile_path is None:
        parser.error(f"未找到 {PROFILE_NAME}；请用 --profile 指定")
    try:
        profile = load_profile(profile_path)
    except ProfileError as error:
        parser.error(str(error))
    profile_errors = validate_profile(profile)
    if profile_errors:
        parser.error("profile 无效：" + "；".join(profile_errors))
    root = local_memory_root(profile, profile_path)
    if root is None:
        parser.error("本地审计要求 memory.provider 为 markdown 或 obsidian")
    attachments = profile["attachments"]
    if attachments.get("mode") != "local":
        parser.error("本地审计要求 attachments.mode 为 local")
    if not root.is_dir():
        parser.error(f"Memory 根目录不存在：{root}")
    for scope in args.scope:
        scope_path = (root / scope).resolve()
        if not scope_path.is_dir() or not scope_path.is_relative_to(root):
            parser.error(f"无效的 --scope：{scope}")
    report = audit(root, args.scope, profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key, value in report.items():
            if isinstance(value, list):
                print(f"{key}: {len(value)}")
                for item in value:
                    print(f"  - {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
            else:
                print(f"{key}: {value}")
    fails = bool(
        report["broken_references"] or report["ambiguous_references"]
        or report["misplaced_images"] or report["unreadable_notes"]
    )
    if attachments.get("link_style") == "markdown" and report["wiki_embeds"]:
        fails = True
    return int(fails)


if __name__ == "__main__":
    raise SystemExit(main())
