#!/usr/bin/env python3
"""Unit tests for paper-tutor's inspect_pdf.py helper."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "inspect_pdf.py"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def create_two_column_test_pdf(path: Path) -> None:
    """Create a synthetic 2-page two-column academic PDF with indented paragraphs and a figure caption."""
    doc = fitz.open()

    # Page 1: Full-width title + Abstract + 2-column Section 1 with two indented paragraphs and cross-page split
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 60), "DemoNet: A Toy Paper for Testing", fontsize=16, fontname="helv")
    p1.insert_text((72, 95), "Abstract", fontsize=12, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(72, 105, 523, 170),
        "This paper demonstrates a clean two-column layout with multiple paragraphs.",
        fontsize=10,
        fontname="helv",
    )

    # Left column: Heading + Paragraph 1 + Indented Paragraph 2 (continuing to right column)
    p1.insert_text((50, 210), "1. Introduction", fontsize=12, fontname="hebo")
    # Manually place lines to mimic LaTeX \parindent with zero \parskip
    left_lines = [
        (50, 230, "First paragraph line one explains the core problem"),
        (50, 242, "in clear terms and finishes cleanly here."),
        (62, 255, "Second paragraph starts indented right below"),
        (50, 267, "without a blank line and continues into"),
    ]
    for x, y, txt in left_lines:
        p1.insert_text((x, y), txt, fontsize=10, fontname="helv")

    # Right column: Continuation of paragraph 2 + Figure 1 caption
    right_lines = [
        (310, 230, "the right column and ends properly here."),
        (310, 260, "Figure 1. Overview of the DemoNet pipeline."),
    ]
    for x, y, txt in right_lines:
        p1.insert_text((x, y), txt, fontsize=10, fontname="helv")

    # Page 2: Section 2 heading + paragraph
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 80), "2. Method", fontsize=12, fontname="hebo")
    p2.insert_text((50, 102), "We formulate the objective over vectors x and y.", fontsize=10, fontname="helv")

    doc.save(str(path))
    doc.close()


class TestInspectPdf(unittest.TestCase):
    def test_locate_prefers_paper_memory_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            papers_dir = root / "papers"
            other_dir = root / "misc"
            papers_dir.mkdir()
            other_dir.mkdir()
            create_two_column_test_pdf(papers_dir / "DemoNet.pdf")
            create_two_column_test_pdf(other_dir / "DemoNet_copy.pdf")
            (root / ".paper-memory.json").write_text(
                json.dumps({"papers": [{"provider": "filesystem", "location": "papers"}]}),
                encoding="utf-8",
            )

            proc = run_cli("locate", "DemoNet", "--root", str(root))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertGreaterEqual(len(data["matches"]), 1)
            self.assertEqual(data["matches"][0]["name"], "DemoNet.pdf")
            self.assertEqual(data["matches"][0]["source"], "paper-memory-dir")

    def test_outline_extract_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf_path = root / "DemoNet.pdf"
            create_two_column_test_pdf(pdf_path)

            # Outline
            p_out = run_cli("outline", str(pdf_path))
            self.assertEqual(p_out.returncode, 0, p_out.stderr)
            outline = json.loads(p_out.stdout)
            self.assertEqual(outline["page_count"], 2)
            self.assertIn("DemoNet", outline["title_guess"])
            headings = [h["title"] for h in outline["detected_headings"]]
            self.assertTrue(any("Introduction" in h for h in headings))
            self.assertTrue(any("Method" in h for h in headings))
            self.assertTrue(outline["pages"][0]["recommend_render"])

            # Extract JSON
            p_ext = run_cli("extract", str(pdf_path), "--pages", "1", "--format", "json")
            self.assertEqual(p_ext.returncode, 0, p_ext.stderr)
            extracted = json.loads(p_ext.stdout)
            texts = [item["text"] for item in extracted["items"]]
            kinds = [item["kind"] for item in extracted["items"]]
            self.assertIn("figure_caption", kinds)
            # First and second indented paragraphs should stay separated
            p1_matches = [t for t in texts if t.startswith("First paragraph line one")]
            p2_matches = [t for t in texts if t.startswith("Second paragraph starts indented")]
            self.assertEqual(len(p1_matches), 1)
            self.assertEqual(len(p2_matches), 1)
            # Second paragraph should stitch cleanly across columns
            self.assertIn("the right column and ends properly here.", p2_matches[0])

            # Render
            render_dir = root / "renders"
            p_ren = run_cli("render", str(pdf_path), "--pages", "1", "--out-dir", str(render_dir))
            self.assertEqual(p_ren.returncode, 0, p_ren.stderr)
            ren = json.loads(p_ren.stdout)
            self.assertEqual(len(ren["rendered"]), 1)
            self.assertTrue(Path(ren["rendered"][0]["path"]).is_file())

            # Safety cap test
            p_cap = run_cli("render", str(pdf_path), "--pages", "1-2", "--max-pages", "1")
            self.assertNotEqual(p_cap.returncode, 0)


if __name__ == "__main__":
    unittest.main()
