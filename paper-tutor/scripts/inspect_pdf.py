#!/usr/bin/env python3
"""Lightweight PDF inspection helper for the paper-tutor skill.

Uses PyMuPDF (fitz) when available to:
1. Locate local PDFs by path or short title (respecting .paper-memory.json if present).
2. Extract a paper outline with page numbers, estimated paragraph counts, and figure/table hints.
3. Extract reading-order logical paragraphs (two-column aware, splitting LaTeX indented paragraphs,
   and stitching cross-column/cross-page continuations).
4. Render selected pages with figures, tables, or dense formulas to PNGs in /tmp for visual inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", ".obsidian", ".trash", ".claudian", "node_modules", "__pycache__", ".venv", "venv"}

CAPTION_RE = re.compile(
    r"^(?:Figure|Fig\.?|Table|Tab\.?|Algorithm|Alg\.?)\s*[A-Z]?\d+[\.:]?\b",
    re.IGNORECASE,
)

SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:Abstract|Introduction|Related\s+Work|Background|Preliminaries|"
    r"Methodology|Method|Methods|Approach|Framework|Model|Architecture|"
    r"Experiments?|Experimental\s+Setup|Evaluation|Results?|Analysis|"
    r"Ablation(?:\s+Study)?|Discussion|Limitations?|Future\s+Work|"
    r"Conclusion|Conclusions?|Acknowledgments?|Acknowledgements?|"
    r"References|Bibliography|Ethics\s+Statement|Broader\s+Impact|"
    r"Supplementary(?:\s+Material)?|Appendix(?:\s+[A-Z0-9]+)?)"
    r"|"
    r"(?:\d+(?:\.\d+){0,3}\.?\s+[A-Z][^\n]{1,100})"
    r"|"
    r"(?:[IVX]+\.\s+[A-Z][^\n]{1,100})"
    r"|"
    r"(?:[A-Z](?:\.\d+){0,2}\.?\s+[A-Z][^\n]{1,100})"
    r")$",
    re.IGNORECASE,
)

TERMINAL_PUNCT_RE = re.compile(r"""[.!?:;]["'\)\]\u201d\u2019]*$""")
PAGE_NUMBER_RE = re.compile(r"^(?:page\s+)?\d{1,4}$", re.IGNORECASE)
BULLET_START_RE = re.compile(r"^(?:[•\u2022\u25cf\u25e6\u2023\-–—*]|\(\d+\)|\d+\))\s+")


def require_fitz():
    try:
        import pymupdf as fitz  # type: ignore
        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError:
            sys.stderr.write(
                "ERROR: PyMuPDF (pymupdf) is not installed in this Python environment. "
                "Fall back to the agent's native PDF reading capability.\n"
            )
            sys.exit(2)


def find_paper_memory_dirs(root: Path) -> list[Path]:
    """Read local paper directories from the nearest .paper-memory.json if present."""
    dirs: list[Path] = []
    cur = root.resolve()
    config_path: Path | None = None
    for parent in [cur, *cur.parents]:
        candidate = parent / ".paper-memory.json"
        if candidate.is_file():
            config_path = candidate
            break
    if not config_path:
        return dirs
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return dirs
    base = config_path.parent
    for entry in data.get("papers", []):
        if isinstance(entry, dict) and entry.get("provider") == "filesystem":
            loc = entry.get("location")
            if loc:
                p = Path(loc)
                resolved = p if p.is_absolute() else (base / p)
                if resolved.is_dir():
                    dirs.append(resolved.resolve())
    return dirs


def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def score_pdf_match(query: str, pdf_path: Path, in_preferred_dir: bool) -> int:
    q_low = query.lower().strip()
    q_norm = normalize_token(query)
    stem_low = pdf_path.stem.lower()
    stem_norm = normalize_token(pdf_path.stem)
    name_low = pdf_path.name.lower()

    score = 0
    if q_low == name_low or q_low == stem_low:
        score = 100
    elif q_norm and q_norm == stem_norm:
        score = 95
    elif q_low in stem_low:
        score = 80
    elif q_norm and q_norm in stem_norm:
        score = 75
    else:
        tokens = [t for t in re.split(r"[\s_\-/]+", q_low) if t]
        if tokens and all(t in stem_low for t in tokens):
            score = 65
        elif tokens:
            hits = sum(1 for t in tokens if t in stem_low)
            if hits > 0:
                score = int(40 * hits / len(tokens))

    if score > 0 and in_preferred_dir:
        score += 10
    return score


def cmd_locate(args: argparse.Namespace) -> int:
    query = args.query.strip()
    root = Path(args.root).resolve()

    direct = Path(query).expanduser()
    if direct.is_file():
        result = {"query": query, "matches": [{"path": str(direct.resolve()), "score": 120, "source": "direct"}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    rel = root / query
    if rel.is_file():
        result = {"query": query, "matches": [{"path": str(rel.resolve()), "score": 120, "source": "direct"}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    preferred_dirs = find_paper_memory_dirs(root)
    seen: set[Path] = set()
    candidates: list[dict[str, Any]] = []

    def scan_dir(base_dir: Path, preferred: bool) -> None:
        for dirpath, dirnames, filenames in os.walk(base_dir):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if not fn.lower().endswith(".pdf"):
                    continue
                p = (Path(dirpath) / fn).resolve()
                if p in seen:
                    continue
                seen.add(p)
                score = score_pdf_match(query, p, preferred)
                if score > 0:
                    candidates.append(
                        {
                            "path": str(p),
                            "name": p.name,
                            "score": score,
                            "source": "paper-memory-dir" if preferred else "workspace",
                        }
                    )

    for d in preferred_dirs:
        scan_dir(d, preferred=True)
    scan_dir(root, preferred=False)

    candidates.sort(key=lambda item: (-item["score"], len(item["name"]), item["name"].lower()))
    top = candidates[: args.limit]
    print(json.dumps({"query": query, "matches": top}, ensure_ascii=False, indent=2))
    return 0 if top else 1


def coalesce_visual_lines(raw_lines: list[dict[str, Any]], page_width: float) -> list[dict[str, Any]]:
    """Filter rotated margin stamps and merge spans/lines that share the same visual baseline."""
    extracted: list[dict[str, Any]] = []
    for ln in raw_lines:
        direction = ln.get("dir", (1.0, 0.0))
        bbox = ln.get("bbox", [0, 0, 0, 0])
        x0, y0, x1, y1 = [float(v) for v in bbox]
        # Skip rotated side-margin stamps (e.g. arXiv vertical identifier on page 1)
        if abs(float(direction[1])) > 0.7 and (x1 < page_width * 0.12 or x0 > page_width * 0.88):
            continue

        parts: list[str] = []
        sizes: list[float] = []
        bold_chars = 0
        total_chars = 0
        for span in ln.get("spans", []):
            txt = span.get("text", "")
            if not txt:
                continue
            parts.append(txt)
            stripped = txt.strip()
            if stripped:
                sz = float(span.get("size", 0.0))
                if sz > 0:
                    sizes.append(sz)
                flags = int(span.get("flags", 0))
                font_name = str(span.get("font", "")).lower()
                is_bold = bool(flags & 16) or ("bold" in font_name) or ("medi" in font_name)
                total_chars += len(stripped)
                if is_bold:
                    bold_chars += len(stripped)

        text = "".join(parts).strip()
        if not text:
            continue
        med_size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
        extracted.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "text": text,
                "font_size": med_size,
                "bold_chars": bold_chars,
                "total_chars": total_chars,
            }
        )

    if not extracted:
        return []

    extracted.sort(key=lambda l: (round(l["y0"], 1), round(l["x0"], 1)))
    visual: list[dict[str, Any]] = []
    for ln in extracted:
        if visual and abs(ln["y0"] - visual[-1]["y0"]) <= 3.0 and ln["x0"] >= visual[-1]["x1"] - 4.0:
            prev = visual[-1]
            prev["text"] = prev["text"] + " " + ln["text"]
            prev["x1"] = max(prev["x1"], ln["x1"])
            prev["y1"] = max(prev["y1"], ln["y1"])
            prev["bold_chars"] += ln["bold_chars"]
            prev["total_chars"] += ln["total_chars"]
            if prev["font_size"] == 0.0:
                prev["font_size"] = ln["font_size"]
        else:
            visual.append(dict(ln))
    return visual


def merge_visual_lines_text(vlines: list[dict[str, Any]]) -> tuple[str, float, bool]:
    if not vlines:
        return "", 0.0, False
    sizes = [l["font_size"] for l in vlines if l["font_size"] > 0]
    bold_chars = sum(l["bold_chars"] for l in vlines)
    total_chars = sum(l["total_chars"] for l in vlines)

    merged = vlines[0]["text"]
    for nxt_line in vlines[1:]:
        nxt = nxt_line["text"]
        if merged.endswith("-") and len(merged) >= 2 and merged[-2].isalpha() and nxt and nxt[0].islower():
            merged = merged[:-1] + nxt
        else:
            merged = merged + " " + nxt
    merged = re.sub(r"\s+", " ", merged).strip()
    median_size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    mostly_bold = total_chars > 0 and (bold_chars / total_chars) >= 0.6
    return merged, round(median_size, 2), mostly_bold


def split_visual_lines_into_paragraphs(vlines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a PyMuPDF text block into true logical paragraphs using indentation & vertical gap cues."""
    if len(vlines) <= 2:
        return [vlines] if vlines else []

    x0_values = sorted(l["x0"] for l in vlines)
    # Left margin is the 20th percentile x0 (robust against a single indented first line)
    base_x0 = x0_values[max(0, int(len(x0_values) * 0.2))]
    max_x1 = max(l["x1"] for l in vlines)
    block_w = max_x1 - base_x0

    dys = [vlines[i]["y0"] - vlines[i - 1]["y0"] for i in range(1, len(vlines)) if vlines[i]["y0"] > vlines[i - 1]["y0"]]
    med_dy = sorted(dys)[len(dys) // 2] if dys else 12.0

    groups: list[list[dict[str, Any]]] = [[vlines[0]]]
    in_bullet_list = bool(BULLET_START_RE.match(vlines[0]["text"]))

    for i in range(1, len(vlines)):
        prev = vlines[i - 1]
        curr = vlines[i]
        dy = curr["y0"] - prev["y0"]
        indent = curr["x0"] - base_x0
        prev_short = block_w > 80 and (max_x1 - prev["x1"]) >= 12.0
        prev_ended = bool(TERMINAL_PUNCT_RE.search(prev["text"].rstrip()))
        curr_is_bullet = bool(BULLET_START_RE.match(curr["text"]))

        if curr_is_bullet:
            in_bullet_list = True
            groups[-1].append(curr)
            continue

        # Continuation lines of a hanging-indented bullet stay in the same list/paragraph
        if in_bullet_list and indent >= 4.0 and not (prev_ended and dy > med_dy * 1.25):
            groups[-1].append(curr)
            continue

        if indent < 4.0:
            in_bullet_list = False

        # Caption inside a block starts its own item
        is_caption_start = bool(CAPTION_RE.match(curr["text"]))
        # First-line indent typical in LaTeX (\parindent ~ 8-22pt)
        is_indented_start = (6.0 <= indent <= 28.0) and (prev_ended or prev_short or dy > med_dy * 1.08)
        # Clear vertical paragraph gap (\parskip)
        is_gap_start = (med_dy > 4.0) and (dy > med_dy * 1.38) and (prev_ended or prev_short)

        if is_caption_start or is_indented_start or is_gap_start:
            groups.append([curr])
        else:
            groups[-1].append(curr)

    return groups


def extract_page_blocks(page: Any) -> list[dict[str, Any]]:
    """Extract logical blocks sorted in two-column-aware reading order."""
    width = float(page.rect.width)
    height = float(page.rect.height)
    mid = width / 2.0
    col_margin = width * 0.04

    raw_dict = page.get_text("dict")
    blocks: list[dict[str, Any]] = []

    for blk in raw_dict.get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        vlines = coalesce_visual_lines(blk.get("lines", []), width)
        if not vlines:
            continue

        para_groups = split_visual_lines_into_paragraphs(vlines)
        for group in para_groups:
            x0 = min(l["x0"] for l in group)
            y0 = min(l["y0"] for l in group)
            x1 = max(l["x1"] for l in group)
            y1 = max(l["y1"] for l in group)
            text, font_size, is_bold = merge_visual_lines_text(group)
            if not text:
                continue

            is_margin_artifact = False
            if (y1 < height * 0.07 or y0 > height * 0.93) and len(text) < 90:
                if PAGE_NUMBER_RE.match(text) or len(text.split()) <= 10:
                    is_margin_artifact = True

            blk_w = x1 - x0
            if blk_w > width * 0.56 or (x0 < mid - col_margin and x1 > mid + col_margin):
                col = "full"
            elif x1 <= mid + col_margin:
                col = "left"
            else:
                col = "right"

            blocks.append(
                {
                    "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "col": col,
                    "text": text,
                    "font_size": font_size,
                    "is_bold": is_bold,
                    "is_margin_artifact": is_margin_artifact,
                }
            )

    body_blocks = [b for b in blocks if not b["is_margin_artifact"]]
    left_count = sum(1 for b in body_blocks if b["col"] == "left" and len(b["text"]) >= 40)
    right_count = sum(1 for b in body_blocks if b["col"] == "right" and len(b["text"]) >= 40)
    is_two_col = left_count >= 1 and right_count >= 1

    if not is_two_col:
        ordered = sorted(body_blocks, key=lambda b: (round(b["y0"], 1), round(b["x0"], 1)))
    else:
        full_blocks = sorted([b for b in body_blocks if b["col"] == "full"], key=lambda b: b["y0"])
        col_blocks = [b for b in body_blocks if b["col"] in ("left", "right")]
        ordered = []

        band_top = -1.0
        for fb in full_blocks:
            fb_mid_y = (fb["y0"] + fb["y1"]) / 2.0
            band = [b for b in col_blocks if band_top < ((b["y0"] + b["y1"]) / 2.0) < fb_mid_y]
            left_band = sorted([b for b in band if b["col"] == "left"], key=lambda b: (b["y0"], b["x0"]))
            right_band = sorted([b for b in band if b["col"] == "right"], key=lambda b: (b["y0"], b["x0"]))
            ordered.extend(left_band)
            ordered.extend(right_band)
            ordered.append(fb)
            band_top = fb_mid_y

        tail = [b for b in col_blocks if ((b["y0"] + b["y1"]) / 2.0) >= band_top]
        left_tail = sorted([b for b in tail if b["col"] == "left"], key=lambda b: (b["y0"], b["x0"]))
        right_tail = sorted([b for b in tail if b["col"] == "right"], key=lambda b: (b["y0"], b["x0"]))
        ordered.extend(left_tail)
        ordered.extend(right_tail)

    return ordered


def classify_block(block: dict[str, Any], body_font_size: float) -> str:
    text = block["text"]
    if CAPTION_RE.match(text):
        lower = text.lower()
        if lower.startswith(("fig", "figure")):
            return "figure_caption"
        if lower.startswith(("tab", "table")):
            return "table_caption"
        return "algorithm_caption"

    is_short = len(text) <= 110 and not text.endswith(".")
    larger_font = body_font_size > 0 and block["font_size"] >= body_font_size + 0.5
    if is_short and (larger_font or block["is_bold"]) and SECTION_HEADING_RE.match(text):
        return "heading"
    if len(text) <= 40 and block["is_bold"] and re.match(r"^(?:abstract|introduction|references|appendix)\b", text, re.I):
        return "heading"
    return "paragraph"


def estimate_body_font_size(doc: Any, sample_pages: int = 8) -> float:
    sizes: list[float] = []
    limit = min(len(doc), sample_pages)
    for idx in range(limit):
        for blk in extract_page_blocks(doc[idx]):
            if len(blk["text"]) >= 60 and blk["font_size"] > 0:
                sizes.append(blk["font_size"])
    if not sizes:
        return 10.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def parse_page_spec(spec: str, max_page: int) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            s_str, e_str = part.split("-", 1)
            start = max(1, int(s_str.strip()))
            end = min(max_page, int(e_str.strip()))
            for p in range(start, end + 1):
                if p not in seen:
                    seen.add(p)
                    pages.append(p)
        else:
            p = int(part)
            if 1 <= p <= max_page and p not in seen:
                seen.add(p)
                pages.append(p)
    return pages


def page_visual_hints(page: Any, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    images = len(page.get_images(full=False))
    drawings = len(page.get_drawings())
    captions = [b["text"][:100] for b in blocks if CAPTION_RE.match(b["text"])]
    recommend_render = bool(captions) or images > 0 or drawings >= 25
    return {
        "image_count": images,
        "drawing_count": drawings,
        "captions": captions,
        "recommend_render": recommend_render,
    }


def cmd_outline(args: argparse.Namespace) -> int:
    fitz = require_fitz()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        sys.stderr.write(f"ERROR: PDF not found: {pdf_path}\n")
        return 1

    doc = fitz.open(str(pdf_path))
    try:
        body_font = estimate_body_font_size(doc)
        toc = [{"level": lvl, "title": title.strip(), "page": pg} for lvl, title, pg in doc.get_toc()]

        detected_headings: list[dict[str, Any]] = []
        pages_summary: list[dict[str, Any]] = []
        title_guess = ""

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            blocks = extract_page_blocks(page)
            if page_num == 1 and blocks:
                largest = max(blocks[:6], key=lambda b: b["font_size"])
                title_guess = largest["text"]

            para_count = 0
            for blk in blocks:
                kind = classify_block(blk, body_font)
                if kind == "heading":
                    detected_headings.append(
                        {
                            "title": blk["text"],
                            "page": page_num,
                            "font_size": blk["font_size"],
                        }
                    )
                elif kind == "paragraph" and len(blk["text"]) >= 40:
                    para_count += 1

            hints = page_visual_hints(page, blocks)
            pages_summary.append(
                {
                    "page": page_num,
                    "paragraphs": para_count,
                    "captions": hints["captions"],
                    "recommend_render": hints["recommend_render"],
                }
            )

        out = {
            "pdf": str(pdf_path),
            "page_count": len(doc),
            "title_guess": title_guess,
            "body_font_size": body_font,
            "pdf_toc": toc,
            "detected_headings": detected_headings,
            "pages": pages_summary,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    finally:
        doc.close()


def should_stitch_continuation(prev: dict[str, Any], curr: dict[str, Any]) -> bool:
    if prev["kind"] != "paragraph" or curr["kind"] != "paragraph":
        return False
    prev_text = prev["text"].rstrip()
    curr_text = curr["text"].lstrip()
    if not prev_text or not curr_text:
        return False
    if TERMINAL_PUNCT_RE.search(prev_text):
        return False
    first = curr_text[0]
    return first.islower() or first in (",", ")", "]")


def cmd_extract(args: argparse.Namespace) -> int:
    fitz = require_fitz()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        sys.stderr.write(f"ERROR: PDF not found: {pdf_path}\n")
        return 1

    doc = fitz.open(str(pdf_path))
    try:
        body_font = estimate_body_font_size(doc)
        if args.pages:
            page_nums = parse_page_spec(args.pages, len(doc))
        else:
            page_nums = list(range(1, len(doc) + 1))

        items: list[dict[str, Any]] = []
        visual_pages: list[dict[str, Any]] = []

        for page_num in page_nums:
            page = doc[page_num - 1]
            blocks = extract_page_blocks(page)
            hints = page_visual_hints(page, blocks)
            if hints["recommend_render"]:
                visual_pages.append({"page": page_num, **hints})

            for blk in blocks:
                kind = classify_block(blk, body_font)
                entry = {
                    "page": page_num,
                    "pages": [page_num],
                    "col": blk["col"],
                    "kind": kind,
                    "text": blk["text"],
                }
                if args.stitch and items and should_stitch_continuation(items[-1], entry):
                    prev = items[-1]
                    if prev["text"].endswith("-") and len(prev["text"]) >= 2 and prev["text"][-2].isalpha():
                        prev["text"] = prev["text"][:-1] + entry["text"]
                    else:
                        prev["text"] = prev["text"] + " " + entry["text"]
                    if page_num not in prev["pages"]:
                        prev["pages"].append(page_num)
                    prev["stitched"] = True
                else:
                    items.append(entry)

        if args.format == "json":
            print(
                json.dumps(
                    {
                        "pdf": str(pdf_path),
                        "pages": page_nums,
                        "visual_pages": visual_pages,
                        "items": items,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"# Extracted: {pdf_path.name} (pages {args.pages or f'1-{len(doc)}'})\n")
            if visual_pages:
                vp_list = ", ".join(str(v["page"]) for v in visual_pages)
                print(f"> Pages with figures/tables/drawings (consider rendering): {vp_list}\n")
            para_idx = 0
            for item in items:
                pg_label = (
                    f"p.{item['pages'][0]}"
                    if len(item["pages"]) == 1
                    else f"p.{item['pages'][0]}-{item['pages'][-1]} (stitched)"
                )
                kind = item["kind"]
                if kind == "heading":
                    print(f"## [{pg_label}] {item['text']}\n")
                elif kind in ("figure_caption", "table_caption", "algorithm_caption"):
                    print(f"**[{kind} | {pg_label}]** {item['text']}\n")
                else:
                    para_idx += 1
                    print(f"[P{para_idx} | {pg_label}] {item['text']}\n")
        return 0
    finally:
        doc.close()


def cmd_render(args: argparse.Namespace) -> int:
    fitz = require_fitz()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        sys.stderr.write(f"ERROR: PDF not found: {pdf_path}\n")
        return 1

    doc = fitz.open(str(pdf_path))
    try:
        page_nums = parse_page_spec(args.pages, len(doc))
        if not page_nums:
            sys.stderr.write("ERROR: No valid pages requested.\n")
            return 1
        if len(page_nums) > args.max_pages:
            sys.stderr.write(
                f"ERROR: Requested {len(page_nums)} pages exceeds --max-pages ({args.max_pages}). "
                "Render in smaller batches for the current section.\n"
            )
            return 1

        if args.out_dir:
            out_dir = Path(args.out_dir).expanduser().resolve()
        else:
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", pdf_path.stem)[:40].strip("_") or "paper"
            digest = hashlib.sha1(str(pdf_path).encode("utf-8")).hexdigest()[:8]
            out_dir = Path(tempfile.gettempdir()) / "paper-tutor" / f"{slug}_{digest}"
        out_dir.mkdir(parents=True, exist_ok=True)

        zoom = args.dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        rendered: list[dict[str, Any]] = []

        for p in page_nums:
            page = doc[p - 1]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_file = out_dir / f"page_{p:03d}.png"
            pix.save(str(out_file))
            rendered.append({"page": p, "path": str(out_file), "width": pix.width, "height": pix.height})

        print(json.dumps({"pdf": str(pdf_path), "out_dir": str(out_dir), "rendered": rendered}, ensure_ascii=False, indent=2))
        return 0
    finally:
        doc.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF inspection helper for paper-tutor.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_loc = sub.add_parser("locate", help="Locate a local PDF by path or short paper name.")
    p_loc.add_argument("query", help="PDF path or short paper name (e.g., Text2Loc, DINOv2).")
    p_loc.add_argument("--root", default=".", help="Workspace root to search (default: current dir).")
    p_loc.add_argument("--limit", type=int, default=5, help="Maximum matches to return.")
    p_loc.set_defaults(func=cmd_locate)

    p_out = sub.add_parser("outline", help="Inspect PDF page count, TOC, headings, and figure/table hints.")
    p_out.add_argument("pdf", help="Path to PDF file.")
    p_out.set_defaults(func=cmd_outline)

    p_ext = sub.add_parser("extract", help="Extract two-column-aware logical paragraphs from selected pages.")
    p_ext.add_argument("pdf", help="Path to PDF file.")
    p_ext.add_argument("--pages", default="", help="Page range spec, e.g., '1-3' or '2,4-6'. Empty = all pages.")
    p_ext.add_argument("--no-stitch", dest="stitch", action="store_false", help="Disable cross-column/cross-page stitching.")
    p_ext.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format.")
    p_ext.set_defaults(func=cmd_extract, stitch=True)

    p_ren = sub.add_parser("render", help="Render selected pages to PNGs in /tmp for visual inspection.")
    p_ren.add_argument("pdf", help="Path to PDF file.")
    p_ren.add_argument("--pages", required=True, help="Page range spec, e.g., '1,3-4'.")
    p_ren.add_argument("--dpi", type=int, default=150, help="Render DPI (default: 150).")
    p_ren.add_argument("--max-pages", type=int, default=8, help="Safety cap per render call (default: 8).")
    p_ren.add_argument("--out-dir", default="", help="Optional output directory (defaults to /tmp/paper-tutor/...).")
    p_ren.set_defaults(func=cmd_render)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
