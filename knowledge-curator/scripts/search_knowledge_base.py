#!/usr/bin/env python3
"""只读检索本地知识库：按 IDF 加权的查询词覆盖率筛选候选文件，并输出命中片段。"""

from __future__ import annotations

import argparse
import fnmatch
import html
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

TEXT_EXTENSIONS = {
    ".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc", ".org", ".tex", ".html", ".htm",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv",
}
MARKDOWN_EXTENSIONS = {".md", ".mdx", ".markdown"}
ZIP_DOCUMENT_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".epub"}
FILENAME_ONLY_EXTENSIONS = {".doc", ".ppt", ".xls"}
SEARCHABLE_EXTENSIONS = TEXT_EXTENSIONS | ZIP_DOCUMENT_EXTENSIONS | FILENAME_ONLY_EXTENSIONS | {".pdf"}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", "node_modules", "vendor",
    ".venv", "venv", "dist", "build", ".cache", ".pytest_cache", ".mypy_cache",
}
MAX_DOCUMENT_BYTES = 50_000_000
PDF_TIMEOUT_SECONDS = 60
BM25_K1 = 1.2
BM25_B = 0.75
PART_WEIGHT = 0.5
HEADING_BOOST = 2
PATH_BOOST = 3
CLIP_WIDTH = 240
WEAK_CANDIDATE_LIMIT = 3

LETTERS = "a-zà-öø-ÿĀ-ɏͰ-ϿЀ-ӿ"
WORD_RE = re.compile(f"[0-9{LETTERS}][0-9{LETTERS}_+#.-]*")
LETTER_RE = re.compile(f"[{LETTERS}]")
ASCII_ALPHA_RE = re.compile("[a-z]+")
SEPARATOR_RE = re.compile("[._-]+")
CJK_RUN_RE = re.compile("[㐀-䶿一-鿿豈-﫿]+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(?:```|~~~)")

EN_STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before being below
    between both but by can could did do does doing done down during each either else etc few for from
    further get gets got had has have having he her here hers herself him himself his how i if in into is
    it its itself just let me might more most must my myself no nor not now of off on once only or other
    our ours ourselves out over own please same shall she should so some such than that the their theirs
    them themselves then there these they this those through to too under until up us very via vs versus
    was we were what when where which while who whom whose why will with would you your yours yourself
    yourselves e.g i.e explain tell show describe work works use used uses using usage way ways mean means
    meaning define definition difference differences example examples thing things something anything
    need needs want know
    """.split()
)
ZH_STOPWORDS = frozenset(
    """
    的 了 吗 呢 吧 啊 呀 嘛 么 是 我 你 他 她 它
    什么 什么样 怎么 怎么样 怎么办 怎样 如何 为什么 为何 哪些 哪个 哪里 哪儿 多少 多大 多久 是否 能否 可否
    有哪些 有什么 有多少 有啥 怎么做 怎么用 如何做 如何用 怎样做 怎样用 该怎么 要怎么 该如何 要如何
    有没有 没有 是不是 能不能 会不会 可不可以 要不要 请问 请教 帮我 帮忙 麻烦 告诉我 了解 知道 一下 一些
    一个 一种 这个 那个 这些 那些 这种 那种 这样 那样 我们 你们 他们 她们 它们 自己 以及 或者 还是 但是
    因为 所以 如果 的话 并且 而且 然后 可以 能够 应该 需要 时候 关于
    """.split()
)
ZH_FRAME_WORDS = sorted(
    """
    区别 差异 用法 作用 原理 工作原理 含义 意思 定义 概念 介绍 解释 说明 讲解 讲讲 说说 聊聊 看看 教程 方法
    步骤 问题 例子 示例 简介 总结 使用 实现 解决 现在 目前 当前
    """.split(),
    key=len,
    reverse=True,
)
ZH_STOPWORD_MAX = max(len(word) for word in ZH_STOPWORDS)
CJK_CONJUNCTIONS = frozenset("和与")
WEAK_CJK_CHARS = frozenset(
    "中里上下内外前后用做写看在和与及或而并等个些这那就都也还又再才去来说想讲弄搞让把被对从到给将向往以为于其之所有要会能可该"
)
EDGE_WEAK_START = frozenset("把被给让将弄搞说讲想做看写用")
EDGE_WEAK_END = frozenset("中里都也时")

DOCX_TEXT_RE = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>|<w:(?:tab|br|cr)\b[^>]*/>")
PPTX_TEXT_RE = re.compile(r"<a:t(?:\s[^>]*)?>([^<]*)</a:t>|<a:br\b[^>]*/>")
XLSX_TEXT_RE = re.compile(r"<t(?:\s[^>]*)?>([^<]*)</t>")
XLSX_ROW_RE = re.compile(r"<row\b([^>]*?)(?:/>|>(.*?)</row>)", re.S)
XLSX_CELL_RE = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
XLSX_VALUE_RE = re.compile(r"<v>([^<]*)</v>")
SLIDE_NAME_RE = re.compile(r"ppt/slides/slide(\d+)\.xml")
WORKSHEET_NAME_RE = re.compile(r"xl/worksheets/sheet(\d+)\.xml")
EPUB_MEMBER_RE = re.compile(r".+\.(?:xhtml|html|htm)", re.I)
HTML_DROP_RE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
HTML_BREAK_RE = re.compile(
    r"<br\s*/?>|</(?:p|div|h[1-6]|li|tr|td|th|dt|dd|blockquote|pre|section|article|figcaption|title)\s*>",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")

FILENAME_ONLY_NOTES = {
    "no_pdf_backend": (
        "未找到可用的 PDF 文本抽取工具（pdftotext / PyMuPDF / pypdf），{count} 个 PDF 只按文件名匹配",
        "未找到可用的 PDF 文本抽取工具（pdftotext / PyMuPDF / pypdf），只按文件名匹配；请用能读取 PDF 的工具查看原件。",
    ),
    "unsupported": (
        "{count} 个旧版 Office 文档只按文件名匹配",
        "旧版 Office 格式，只按文件名匹配；请用对应格式工具读取原件。",
    ),
    "too_large": (
        "{count} 个文件超过大小上限，只按文件名匹配：{paths}",
        "超过大小上限，只按文件名匹配；请直接打开原件定向查看。",
    ),
    "failed": (
        "{count} 个文件无法读取或解析，只按文件名匹配：{paths}",
        "无法读取或解析，只按文件名匹配；请用对应格式工具检查原件。",
    ),
}


class DocumentError(Exception):
    pass


READ_ERRORS = (
    OSError, RuntimeError, ValueError, KeyError, EOFError, zlib.error, zipfile.BadZipFile,
    subprocess.SubprocessError, DocumentError,
)


@dataclass
class Query:
    terms: list = field(default_factory=list)
    factor: dict = field(default_factory=dict)
    display: dict = field(default_factory=dict)
    ignored: list = field(default_factory=list)
    fallback: bool = False

    def add(self, term: str, factor: float, shown: str) -> None:
        if term in self.factor:
            self.factor[term] = max(self.factor[term], factor)
            return
        self.terms.append(term)
        self.factor[term] = factor
        self.display[term] = shown

    @property
    def word_terms(self) -> set:
        return {term for term in self.terms if not CJK_RUN_RE.fullmatch(term)}

    @property
    def cjk_terms(self) -> list:
        return [term for term in self.terms if CJK_RUN_RE.fullmatch(term)]

    @property
    def needles(self) -> list:
        # 文档侧把 policies 归一为 policy，预筛时去掉末尾 y 才能在原文中命中。
        return [term[:-1] if term.endswith("y") and ASCII_ALPHA_RE.fullmatch(term) else term for term in self.terms]

    def label(self, term: str) -> str:
        shown = self.display[term]
        return shown if self.factor[term] >= 1 else f"{shown}({self.factor[term]:g})"


@dataclass
class Hit:
    index: int
    terms: frozenset
    heading: str
    heading_terms: frozenset


@dataclass
class Document:
    path: Path
    kind: str
    reason: Optional[str]
    lines: list
    labels: Optional[list]
    tf: Counter
    heading_tf: Counter
    path_terms: set
    length: int
    hits: list
    matched: list = field(default_factory=list)
    coverage: float = 0.0
    score: float = 0.0


@dataclass
class Corpus:
    documents: list = field(default_factory=list)
    total: int = 0
    total_length: int = 0
    content_count: int = 0
    notes: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def canonical(token: str) -> str:
    if len(token) < 4 or not ASCII_ALPHA_RE.fullmatch(token):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("sses"):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def word_atoms(token: str) -> list:
    return [atom for atom in SEPARATOR_RE.split(token) if len(atom) > 1 and LETTER_RE.search(atom)]


def expansions(token: str) -> list:
    pieces = []
    if "." in token:
        dotted = token.split(".")
        pieces.extend(".".join(dotted[:end]) for end in range(2, len(dotted)))
        pieces.extend(piece.strip("_-") for piece in dotted if SEPARATOR_RE.search(piece.strip("_-")))
    pieces.extend(word_atoms(token))
    main = canonical(token)
    result = []
    for piece in pieces:
        piece = canonical(piece)
        if piece and piece != main and piece not in result:
            result.append(piece)
    return result


def word_tokens(text: str) -> list:
    tokens = []
    for raw in WORD_RE.findall(text):
        token = raw.rstrip("._-")
        tokens.append(canonical(token))
        if SEPARATOR_RE.search(token):
            tokens.extend(expansions(token))
    return tokens


def cjk_length(text: str) -> int:
    return sum(len(run) for run in CJK_RUN_RE.findall(text))


def split_stopwords(run: str, ignored: list) -> list:
    segments, current, index = [], [], 0
    while index < len(run):
        for size in range(min(ZH_STOPWORD_MAX, len(run) - index), 0, -1):
            piece = run[index : index + size]
            if piece in ZH_STOPWORDS:
                if current:
                    segments.append("".join(current))
                    current = []
                ignored.append(piece)
                index += size
                break
        else:
            current.append(run[index])
            index += 1
    if current:
        segments.append("".join(current))
    return segments


def split_conjunctions(segment: str) -> list:
    parts, start = [], 0
    for index, char in enumerate(segment):
        if char in CJK_CONJUNCTIONS and index - start >= 2 and len(segment) - index > 2:
            parts.append(segment[start:index])
            start = index + 1
    parts.append(segment[start:])
    return parts


def strip_frame_words(segment: str, ignored: list) -> str:
    while segment:
        if segment in ZH_FRAME_WORDS:
            ignored.append(segment)
            return ""
        for word in ZH_FRAME_WORDS:
            if len(segment) - len(word) < 2:
                continue
            if segment.startswith(word):
                segment = segment[len(word) :]
                break
            if segment.endswith(word):
                segment = segment[: -len(word)]
                break
        else:
            return segment
        ignored.append(word)
    return segment


def cjk_query_terms(segment: str) -> list:
    if len(segment) == 1:
        return [(segment, 1.0)]
    last = len(segment) - 2
    terms = []
    for index in range(last + 1):
        gram = segment[index : index + 2]
        weak = len(segment) > 2 and (
            (index == 0 and gram[0] in EDGE_WEAK_START) or (index == last and gram[1] in EDGE_WEAK_END)
        )
        terms.append((gram, PART_WEIGHT if weak else 1.0))
    return terms


def is_stopword(token: str) -> bool:
    return token in EN_STOPWORDS or canonical(token) in EN_STOPWORDS


def build_query(text: str) -> Query:
    norm = normalize(text)
    query = Query()
    raw_words = [raw.rstrip("._-") for raw in WORD_RE.findall(norm)]
    cjk_runs = CJK_RUN_RE.findall(norm)

    for token in raw_words:
        if len(token) < 2 or is_stopword(token):
            query.ignored.append(token)
            continue
        query.add(canonical(token), 1.0, token)
        if SEPARATOR_RE.search(token):
            for atom in word_atoms(token):
                if not is_stopword(atom):
                    query.add(canonical(atom), PART_WEIGHT, atom)
    for run in cjk_runs:
        for segment in split_stopwords(run, query.ignored):
            for part in split_conjunctions(segment):
                part = strip_frame_words(part, query.ignored)
                if not part:
                    continue
                if all(char in WEAK_CJK_CHARS for char in part):
                    query.ignored.append(part)
                    continue
                for term, factor in cjk_query_terms(part):
                    query.add(term, factor, term)

    if not query.terms and (raw_words or cjk_runs):
        query = Query(fallback=True)
        for token in raw_words:
            query.add(canonical(token), 1.0, token)
        for run in cjk_runs:
            for term, _ in cjk_query_terms(run):
                query.add(term, 1.0, term)
    return query


def path_is_excluded(relative: Path, patterns: list, is_dir: bool = False) -> bool:
    text = relative.as_posix()
    candidates = [text, relative.name] + ([text + "/"] if is_dir else [])
    return any(fnmatch.fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates)


def iter_candidates(root: Path, include_hidden: bool, exclude: list) -> Iterator[Path]:
    for current, directories, filenames in os.walk(root):
        current_path = Path(current)
        kept = []
        for directory in sorted(directories):
            if directory in SKIP_DIRS or (not include_hidden and directory.startswith(".")):
                continue
            if path_is_excluded((current_path / directory).relative_to(root), exclude, is_dir=True):
                continue
            kept.append(directory)
        directories[:] = kept
        for filename in sorted(filenames):
            path = current_path / filename
            if not include_hidden and filename.startswith("."):
                continue
            if path.suffix.casefold() not in SEARCHABLE_EXTENSIONS or path.is_symlink():
                continue
            if not path_is_excluded(path.relative_to(root), exclude):
                yield path


class ZipReader:
    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.names = set(archive.namelist())
        self.consumed = 0

    def read(self, name: str) -> str:
        info = self.archive.getinfo(name)
        self.consumed += info.file_size
        if self.consumed > MAX_DOCUMENT_BYTES:
            raise DocumentError("解压后内容过大")
        return self.archive.read(info).decode("utf-8", errors="replace")


def xml_attribute(tag: str, name: str) -> str:
    match = re.search(rf'(?:^|\s){re.escape(name)}="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else ""


def relationship_targets(reader: ZipReader, rels_name: str, base: str) -> dict:
    if rels_name not in reader.names:
        return {}
    targets = {}
    for tag in re.findall(r"<Relationship\b[^>]*>", reader.read(rels_name)):
        rel_id, target = xml_attribute(tag, "Id"), xml_attribute(tag, "Target")
        if rel_id and target:
            member = target.lstrip("/") if target.startswith("/") else posixpath.join(base, target)
            targets[rel_id] = posixpath.normpath(member)
    return targets


def numbered_members(reader: ZipReader, pattern: re.Pattern) -> list:
    numbered = []
    for name in reader.names:
        match = pattern.fullmatch(name)
        if match:
            numbered.append((int(match.group(1)), name))
    return [name for _, name in sorted(numbered)]


def ordered_parts(reader: ZipReader, main: str, rels: str, base: str, tag_name: str, fallback: re.Pattern) -> list:
    parts = []
    if main in reader.names:
        targets = relationship_targets(reader, rels, base)
        for tag in re.findall(rf"<{tag_name}\b[^>]*>", reader.read(main)):
            member = targets.get(xml_attribute(tag, "r:id"))
            if member in reader.names:
                parts.append((xml_attribute(tag, "name"), member))
    return parts or [("", name) for name in numbered_members(reader, fallback)]


def run_text(matches: Iterable) -> str:
    text = "".join(match.group(1) if match.group(1) is not None else " " for match in matches)
    return " ".join(html.unescape(text).split())


def docx_units(reader: ZipReader) -> list:
    units = []
    for chunk in reader.read("word/document.xml").split("</w:p>"):
        text = run_text(DOCX_TEXT_RE.finditer(chunk))
        if text:
            units.append((f"段落 {len(units) + 1}", text))
    return units


def pptx_units(reader: ZipReader) -> list:
    slides = ordered_parts(
        reader, "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "ppt", "p:sldId", SLIDE_NAME_RE
    )
    units = []
    for number, (_, member) in enumerate(slides, start=1):
        for chunk in reader.read(member).split("</a:p>"):
            text = run_text(PPTX_TEXT_RE.finditer(chunk))
            if text:
                units.append((f"幻灯片 {number}", text))
    return units


def xlsx_units(reader: ZipReader) -> list:
    shared = []
    if "xl/sharedStrings.xml" in reader.names:
        xml = re.sub(r"<rPh\b.*?</rPh>", "", reader.read("xl/sharedStrings.xml"), flags=re.S)
        shared = [html.unescape("".join(XLSX_TEXT_RE.findall(chunk))) for chunk in xml.split("</si>")[:-1]]
    sheets = ordered_parts(reader, "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl", "sheet", WORKSHEET_NAME_RE)
    units = []
    for sheet_name, member in sheets:
        sheet_name = sheet_name or posixpath.basename(member)[:-4]
        for row_number, row in enumerate(XLSX_ROW_RE.finditer(reader.read(member)), start=1):
            cells = []
            for cell in XLSX_CELL_RE.finditer(row.group(2) or ""):
                cell_type, body = xml_attribute(cell.group(1), "t"), cell.group(2) or ""
                if cell_type == "inlineStr":
                    value = html.unescape("".join(XLSX_TEXT_RE.findall(body)))
                else:
                    match = XLSX_VALUE_RE.search(body)
                    value = html.unescape(match.group(1)) if match else ""
                    if cell_type == "s":
                        index = int(value) if value.strip().isdigit() else -1
                        value = shared[index] if 0 <= index < len(shared) else ""
                value = " ".join(value.split())
                if value:
                    cells.append(value)
            if cells:
                row_label = xml_attribute(row.group(1), "r") or str(row_number)
                units.append((f"{sheet_name} 第 {row_label} 行", " | ".join(cells)))
    return units


def epub_units(reader: ZipReader) -> list:
    units = []
    for name in sorted(name for name in reader.names if EPUB_MEMBER_RE.fullmatch(name)):
        markup = HTML_BREAK_RE.sub("\n", HTML_DROP_RE.sub(" ", reader.read(name)))
        for line in html.unescape(TAG_RE.sub(" ", markup)).splitlines():
            line = " ".join(line.split())
            if line:
                units.append((posixpath.basename(name), line))
    return units


def detect_pdf_backend() -> tuple[Optional[str], object]:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        return "pdftotext", pdftotext
    if os.environ.get("KC_DISABLE_PYTHON_PDF") == "1":
        return None, None
    try:
        import fitz  # type: ignore
        if hasattr(fitz, "TOOLS") and hasattr(fitz.TOOLS, "mupdf_display_errors"):
            fitz.TOOLS.mupdf_display_errors(False)
        return "fitz", fitz
    except Exception:
        pass
    try:
        import logging
        import pypdf  # type: ignore
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        return "pypdf", pypdf
    except Exception:
        return None, None


def _pages_to_units(pages: Iterable[str]) -> list:
    units = []
    for page, text in enumerate(pages, start=1):
        for line in (text or "").splitlines():
            line = " ".join(line.split())
            if line:
                units.append((f"第 {page} 页", line))
    return units


def pdf_units(path: Path, backend: tuple[Optional[str], object]) -> list:
    kind, handle = backend
    if kind == "pdftotext":
        completed = subprocess.run(
            [str(handle), "-q", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=PDF_TIMEOUT_SECONDS
        )
        if completed.returncode != 0:
            raise DocumentError(f"pdftotext 退出码 {completed.returncode}")
        return _pages_to_units(completed.stdout.decode("utf-8", errors="replace").split("\f"))
    if kind == "fitz":
        try:
            with handle.open(str(path)) as doc:
                if doc.is_encrypted:
                    raise DocumentError("PDF 已加密")
                return _pages_to_units(page.get_text("text") for page in doc)
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError(f"PyMuPDF 解析失败：{exc}") from exc
    if kind == "pypdf":
        try:
            reader = handle.PdfReader(str(path))
            if reader.is_encrypted:
                raise DocumentError("PDF 已加密")
            return _pages_to_units((page.extract_text() or "") for page in reader.pages)
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError(f"pypdf 解析失败：{exc}") from exc
    raise DocumentError("无可用 PDF 解析后端")


def extract_units(path: Path, suffix: str, pdf_backend: tuple[Optional[str], object]) -> list:
    if suffix == ".pdf":
        return pdf_units(path, pdf_backend)
    with zipfile.ZipFile(path) as archive:
        extractor = {".docx": docx_units, ".pptx": pptx_units, ".xlsx": xlsx_units}.get(suffix, epub_units)
        return extractor(ZipReader(archive))


def path_terms_for(relative: Path, query: Query) -> set:
    norm = normalize(relative.with_suffix("").as_posix())
    word_terms = query.word_terms
    found = {token for token in word_tokens(norm) if token in word_terms}
    found.update(term for term in query.cjk_terms if term in norm)
    return found


def analyze(lines: list, query: Query, detect_headings: bool) -> tuple:
    text = normalize("\n".join(lines))
    length = len(WORD_RE.findall(text)) + cjk_length(text)
    tf, heading_tf, hits = Counter(), Counter(), []
    if not any(needle in text for needle in query.needles):
        return tf, heading_tf, hits, length
    word_terms, cjk_terms = query.word_terms, query.cjk_terms
    heading, heading_terms, in_fence = "", frozenset(), False
    for index, line in enumerate(lines):
        norm = normalize(line)
        matched = set()
        for token in word_tokens(norm):
            if token in word_terms:
                tf[token] += 1
                matched.add(token)
        for term in cjk_terms:
            count = norm.count(term)
            if count:
                tf[term] += count
                matched.add(term)
        if detect_headings:
            if FENCE_RE.match(line):
                in_fence = not in_fence
            elif not in_fence:
                heading_match = HEADING_RE.match(line)
                if heading_match:
                    heading, heading_terms = heading_match.group(1).strip(), frozenset(matched)
                    heading_tf.update(matched)
        if matched:
            hits.append(Hit(index, frozenset(matched), heading, heading_terms))
    return tf, heading_tf, hits, length


def load_document(
    path: Path, root: Path, query: Query, max_bytes: int, pdf_backend: tuple[Optional[str], object], corpus: Corpus
) -> Optional[Document]:
    relative = path.relative_to(root)
    suffix = path.suffix.casefold()
    lines, labels, reason = [], None, None
    try:
        size = path.stat().st_size
        if suffix in TEXT_EXTENSIONS:
            if size > max_bytes:
                reason = "too_large"
            else:
                lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        elif suffix in FILENAME_ONLY_EXTENSIONS:
            reason = "unsupported"
        elif suffix == ".pdf" and pdf_backend[0] is None:
            reason = "no_pdf_backend"
        elif size > MAX_DOCUMENT_BYTES:
            reason = "too_large"
        else:
            units = extract_units(path, suffix, pdf_backend)
            labels = [label for label, _ in units]
            lines = [text for _, text in units]
    except READ_ERRORS:
        reason, lines, labels = "failed", [], None

    corpus.total += 1
    if reason:
        corpus.notes.setdefault(reason, []).append(relative.as_posix())
    tf, heading_tf, hits, length = analyze(lines, query, labels is None and suffix in MARKDOWN_EXTENSIONS)
    if reason is None:
        corpus.content_count += 1
        corpus.total_length += length
    path_terms = path_terms_for(relative, query)
    if not tf and not path_terms:
        return None
    return Document(
        path=relative,
        kind="text" if suffix in TEXT_EXTENSIONS else suffix.lstrip("."),
        reason=reason,
        lines=lines,
        labels=labels,
        tf=tf,
        heading_tf=heading_tf,
        path_terms=path_terms,
        length=length,
        hits=hits,
    )


def term_weights(query: Query, documents: list, total: int) -> dict:
    df = Counter()
    for document in documents:
        df.update(set(document.tf) | document.path_terms)
    weights = {}
    for term in query.terms:
        frequency = max(df[term], 1)
        weights[term] = math.log(1 + (total - frequency + 0.5) / (frequency + 0.5)) * query.factor[term]
    return weights


def rank(corpus: Corpus, query: Query, weights: dict) -> list:
    total_weight = sum(weights.values())
    average = corpus.total_length / corpus.content_count if corpus.content_count else 0.0
    for document in corpus.documents:
        document.matched = [term for term in query.terms if document.tf[term] or term in document.path_terms]
        document.coverage = sum(weights[term] for term in document.matched) / total_weight
        norm = 1 - BM25_B + BM25_B * (document.length / average if average else 0.0)
        bm25 = 0.0
        for term in document.matched:
            frequency = document.tf[term] + HEADING_BOOST * document.heading_tf[term]
            frequency += PATH_BOOST if term in document.path_terms else 0
            bm25 += weights[term] * frequency * (BM25_K1 + 1) / (frequency + BM25_K1 * norm)
        document.score = bm25 * document.coverage
    return sorted(corpus.documents, key=lambda item: (-item.score, item.path.as_posix()))


def select_snippets(document: Document, weights: dict, context: int, limit: int) -> list:
    scored = []
    for hit in document.hits:
        score = sum(weights[term] for term in hit.terms)
        score += 0.5 * sum(weights[term] for term in hit.heading_terms - hit.terms)
        scored.append((-score, hit.index, hit))
    windows = []
    for _, _, hit in sorted(scored, key=lambda item: (item[0], item[1])):
        start = max(0, hit.index - context)
        end = min(len(document.lines), hit.index + context + 1)
        if any(start < other_end and other_start < end for other_start, other_end, _ in windows):
            continue
        windows.append((start, end, hit))
        if len(windows) >= limit:
            break
    return sorted(windows, key=lambda item: item[0])


def clip(line: str, needles: list) -> str:
    if len(line) <= CLIP_WIDTH:
        return line
    lowered = normalize(line)
    positions = [lowered.find(needle) for needle in needles if needle and needle in lowered]
    start = max(0, min(positions, default=0) - CLIP_WIDTH // 3)
    start = min(start, max(0, len(line) - CLIP_WIDTH))
    end = start + CLIP_WIDTH
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


def locator(document: Document, start: int, end: int, hit: Hit) -> str:
    if document.labels is not None:
        return document.labels[hit.index]
    span = f"L{start + 1}" if end == start + 1 else f"L{start + 1}-{end}"
    return f"{span} | {hit.heading}" if hit.heading else span


def describe(rank_number: int, document: Document, query: Query) -> None:
    tags = [document.kind] + (["仅文件名"] if document.reason else [])
    print(
        f"\n{rank_number}. {document.path.as_posix()} "
        f"[{', '.join(tags)}, score={document.score:.2f}, coverage={document.coverage:.2f}]"
    )
    print(f"   matched: {', '.join(query.label(term) for term in document.matched)}")
    missing = [query.label(term) for term in query.terms if term not in document.matched]
    if missing:
        print(f"   missing: {', '.join(missing)}")


def print_snippets(document: Document, query: Query, weights: dict, context: int, limit: int) -> None:
    if document.reason:
        print(f"   {FILENAME_ONLY_NOTES[document.reason][1]}")
        return
    windows = select_snippets(document, weights, context, limit)
    if not windows:
        print("   正文未命中查询词，只匹配到路径。")
        return
    for start, end, hit in windows:
        while start < hit.index and not document.lines[start].strip():
            start += 1
        while end - 1 > hit.index and not document.lines[end - 1].strip():
            end -= 1
        needles = [query.display[term] for term in hit.terms] + sorted(hit.terms)
        print(f"   {locator(document, start, end, hit)}")
        for line in document.lines[start:end]:
            print(f"     {clip(line.rstrip(), needles)}")


def parse_args(argv: Optional[list]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读检索本地知识库，按 IDF 加权的查询词覆盖率筛选并排序候选文件。")
    parser.add_argument("query", help="自然语言问题或检索短语")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="知识库根目录（默认当前目录）")
    parser.add_argument("--limit", type=int, default=10, help="最多输出的候选文件数")
    parser.add_argument("--min-coverage", type=float, default=0.6, help="候选需达到的 IDF 加权查询词覆盖率，取值 (0, 1]")
    parser.add_argument("--context", type=int, default=2, help="每个命中行前后附带的上下文行数")
    parser.add_argument("--snippets-per-file", type=int, default=2, help="每个文件最多输出的片段数")
    parser.add_argument("--max-bytes", type=int, default=2_000_000, help="文本文件超过该字节数时只按文件名匹配")
    parser.add_argument("--exclude", action="append", default=[], help="排除的 glob（相对根目录），可重复使用")
    parser.add_argument("--include-hidden", action="store_true", help="同时检索隐藏文件和目录")
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"错误：知识库根目录不存在或不是目录：{root}", file=sys.stderr)
        return 2
    if args.limit < 1 or args.snippets_per_file < 1 or args.max_bytes < 1 or args.context < 0:
        print("错误：--limit、--snippets-per-file、--max-bytes 必须为正数，--context 不能为负数", file=sys.stderr)
        return 2
    if not 0 < args.min_coverage <= 1:
        print("错误：--min-coverage 必须在 (0, 1] 内", file=sys.stderr)
        return 2
    query = build_query(args.query)
    if not query.terms:
        print("错误：查询中没有可检索的词", file=sys.stderr)
        return 2

    pdf_backend = detect_pdf_backend()
    corpus = Corpus()
    for path in iter_candidates(root, args.include_hidden, args.exclude):
        document = load_document(path, root, query, args.max_bytes, pdf_backend, corpus)
        if document:
            corpus.documents.append(document)
    weights = term_weights(query, corpus.documents, corpus.total)
    ranked = rank(corpus, query, weights)
    results = [document for document in ranked if document.coverage >= args.min_coverage]

    print(f"root: {root}")
    print(f"query: {args.query}")
    print(f"search_terms: {', '.join(query.label(term) for term in query.terms)}")
    if query.ignored:
        print(f"ignored_terms: {', '.join(dict.fromkeys(query.ignored))}")
    print(f"min_coverage: {args.min_coverage:g}")
    if query.fallback:
        print("note: 查询只含停用词，已按原词检索")
    for reason, (summary, _) in FILENAME_ONLY_NOTES.items():
        paths = corpus.notes.get(reason)
        if paths:
            shown = ", ".join(paths[:5]) + (" 等" if len(paths) > 5 else "")
            print(f"note: {summary.format(count=len(paths), paths=shown)}")

    if not results:
        print("results: none")
        weak = [document for document in ranked if document.coverage > 0][:WEAK_CANDIDATE_LIMIT]
        if weak:
            print(f"weak_candidates: {len(weak)}（覆盖率未达阈值，只能作为定向复查线索）")
            for rank_number, document in enumerate(weak, start=1):
                describe(rank_number, document, query)
        return 1

    shown = results[: args.limit]
    print(f"results: {len(shown)}")
    if len(results) > len(shown):
        print(f"note: 另有 {len(results) - len(shown)} 个候选达到阈值但未显示，可调大 --limit")
    for rank_number, document in enumerate(shown, start=1):
        describe(rank_number, document, query)
        print_snippets(document, query, weights, args.context, args.snippets_per_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
