import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "search_knowledge_base.py"
RESULT_LINE = re.compile(r"^\d+\. (.+?) \[", re.M)

NOTES = {
    "生活/cooking.md": (
        "# How does the oven work in my kitchen?\n\n"
        "How does the timer work? The loop of the day is: in the morning, how does the bread rise in the oven?\n"
        "What does the recipe do in the end? The work in the kitchen is how it does.\n"
    ),
    "深度学习/pytorch-training.md": (
        "# PyTorch 训练循环\n\n"
        "一个标准的 PyTorch training loop 包括前向传播、计算 loss、`optimizer.zero_grad()`、"
        "`loss.backward()` 和 `optimizer.step()`。\n\n"
        "## 梯度累积\n\n"
        "显存不够时，连续累积多个 mini-batch 的梯度后再调用 `optimizer.step()`，并把 loss 除以累积步数。\n"
    ),
    "深度学习/lr-scheduler.md": (
        "# Learning rate scheduler\n\n"
        "A learning rate scheduler adjusts the learning rate during training, e.g. cosine annealing.\n"
    ),
    "工程/进程与线程.md": "# 进程与线程\n\n进程是资源分配的基本单位，线程是调度的基本单位。\n",
}


def load_module():
    spec = importlib.util.spec_from_file_location("search_knowledge_base", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def write_files(root, files):
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def write_zip(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def build_docx(path, paragraphs):
    body = "".join(
        "<w:p>" + "".join(f'<w:r><w:t xml:space="preserve">{escape(run)}</w:t></w:r>' for run in runs) + "</w:p>"
        for runs in paragraphs
    )
    write_zip(path, {
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>"
        ),
    })


def slide_xml(text):
    return (
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody>'
        f"<a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def build_pptx(path):
    # presentation.xml 把 slide2.xml 排在第一张，验证按演示顺序编号而不是按文件名。
    write_zip(path, {
        "ppt/presentation.xml": (
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" Type="slide" Target="slides/slide2.xml"/></Relationships>'
        ),
        "ppt/slides/slide1.xml": slide_xml("向量数据库用近似最近邻索引加速检索"),
        "ppt/slides/slide2.xml": slide_xml("季度路线图"),
    })


def build_xlsx(path):
    write_zip(path, {
        "xl/workbook.xml": (
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="选型对比" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": (
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>场景</t></si><si><t>向量检索</t></si></sst>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="inlineStr"><is><t>Milvus</t></is></c>'
            '<c r="C2"><v>42</v></c></row></sheetData></worksheet>'
        ),
    })


class TokenizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_module()

    def tokens(self, text):
        return self.m.word_tokens(self.m.normalize(text))

    def test_symbols_survive_and_trailing_dot_is_stripped(self):
        tokens = self.tokens("Use PyTorch. Then C++, C#, Node.js and GPT-4.")
        for expected in ("pytorch", "c++", "c#", "node.js", "node", "js", "gpt-4", "gpt"):
            self.assertIn(expected, tokens)
        self.assertNotIn("pytorch.", tokens)
        self.assertNotIn("gpt-4.", tokens)

    def test_dotted_prefixes_and_plurals(self):
        tokens = self.tokens("os.path.join loops classes policies")
        for expected in ("os.path", "os.path.join", "loop", "class", "policy"):
            self.assertIn(expected, tokens)

    def test_english_stopwords_are_dropped(self):
        query = self.m.build_query("How does the training loop work in PyTorch?")
        self.assertEqual(query.terms, ["training", "loop", "pytorch"])

    def test_compound_parts_get_half_weight(self):
        query = self.m.build_query("gpt-4")
        self.assertEqual(query.terms, ["gpt-4", "gpt"])
        self.assertEqual(query.factor, {"gpt-4": 1.0, "gpt": 0.5})

    def test_chinese_queries_drop_function_words_and_keep_terms(self):
        cases = {
            "进程和线程的区别": ["进程", "线程"],
            "PyTorch 里怎么做梯度累积？": ["pytorch", "梯度", "度累", "累积"],
            "Python 装饰器的作用是什么": ["python", "装饰", "饰器"],
            "JavaScript 作用域是什么": ["javascript", "作用", "用域"],
        }
        for text, expected in cases.items():
            self.assertEqual(self.m.build_query(text).terms, expected, text)
        query = self.m.build_query("为什么要把 loss 除以累积步数")
        self.assertNotIn("要把", query.terms)
        self.assertIn("步数", query.terms)

    def test_all_stopword_query_falls_back_to_raw_terms(self):
        query = self.m.build_query("how does it work")
        self.assertTrue(query.fallback)
        self.assertEqual(len(query.terms), 4)
        self.assertEqual(self.m.build_query("？？？").terms, [])


class SearchCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.kb = self.base / "kb"
        write_files(self.kb, NOTES)
        self.empty_bin = self.base / "empty-bin"
        self.empty_bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_search(self, query, *args, root=None, path=None, extra_env=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PATH=str(path or self.empty_bin))
        if extra_env:
            env.update(extra_env)
        command = [sys.executable, str(SCRIPT), query, "--root", str(root or self.kb), *args]
        return subprocess.run(command, capture_output=True, encoding="utf-8", env=env)

    def result_paths(self, stdout):
        return RESULT_LINE.findall(stdout)

    def test_topic_note_outranks_stopword_stuffed_note(self):
        result = self.run_search("How does the training loop work in PyTorch?")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("search_terms: training, loop, pytorch", result.stdout)
        self.assertEqual(self.result_paths(result.stdout)[0], "深度学习/pytorch-training.md")

    def test_off_topic_query_exits_1_with_weak_candidates(self):
        result = self.run_search("Kubernetes scheduler")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("results: none", result.stdout)
        self.assertIn("weak_candidates:", result.stdout)
        self.assertIn("深度学习/lr-scheduler.md", result.stdout)
        self.assertIn("missing: kubernetes", result.stdout)

        relaxed = self.run_search("Kubernetes scheduler", "--min-coverage", "0.3")
        self.assertEqual(relaxed.returncode, 0, relaxed.stdout + relaxed.stderr)
        self.assertEqual(self.result_paths(relaxed.stdout), ["深度学习/lr-scheduler.md"])

    def test_chinese_queries(self):
        result = self.run_search("PyTorch 里怎么做梯度累积？")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_paths(result.stdout)[0], "深度学习/pytorch-training.md")
        self.assertIn("| 梯度累积", result.stdout)

        result = self.run_search("进程和线程的区别")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_paths(result.stdout)[0], "工程/进程与线程.md")

        result = self.run_search("Kubernetes 调度器怎么配置")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("results: none", result.stdout)

    def test_all_stopword_query_is_searched_not_rejected(self):
        result = self.run_search("how does it work")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note: 查询只含停用词", result.stdout)
        self.assertEqual(self.result_paths(result.stdout)[0], "生活/cooking.md")

    def test_exclude_prunes_paths_and_directories(self):
        write_files(self.kb, {"drafts/pytorch-draft.md": "# PyTorch training loop draft\n\nPyTorch training loop notes.\n"})
        self.assertIn("drafts/pytorch-draft.md", self.run_search("pytorch training loop").stdout)
        for pattern in ("drafts/**", "drafts", "*-draft.md"):
            result = self.run_search("pytorch training loop", "--exclude", pattern)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("drafts/", result.stdout, pattern)

    def test_headings_inside_code_fences_are_ignored(self):
        write_files(self.kb, {"工程/snippets.md": "# 真实标题\n\n```python\n# not a heading\nfrobnicate_widget()\n```\n"})
        result = self.run_search("frobnicate_widget")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("| 真实标题", result.stdout)
        self.assertNotIn("| not a heading", result.stdout)

    def test_office_documents_are_extracted_with_locations(self):
        docs = self.base / "docs"
        build_docx(docs / "会议纪要.docx", [["项目周会"], ["讨论", "量子", "退火 &amp; 调度"]])
        build_pptx(docs / "分享.pptx")
        build_xlsx(docs / "选型.xlsx")
        cases = [
            ("量子退火", "会议纪要.docx", "段落 2", "讨论量子退火 &amp; 调度"),
            ("向量数据库", "分享.pptx", "幻灯片 2", "向量数据库用近似最近邻索引加速检索"),
            ("Milvus 向量检索", "选型.xlsx", "选型对比 第 2 行", "向量检索 | Milvus | 42"),
        ]
        for query, name, location, text in cases:
            result = self.run_search(query, root=docs)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.result_paths(result.stdout)[0], name)
            self.assertIn(location, result.stdout)
            self.assertIn(text, result.stdout)

    def test_pdf_without_any_backend_matches_filename_only(self):
        write_files(self.kb, {"papers/sparse-attention.pdf": b"%PDF-1.4 not really a pdf"})
        result = self.run_search("sparse attention", extra_env={"KC_DISABLE_PYTHON_PDF": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note: 未找到可用的 PDF 文本抽取工具（pdftotext / PyMuPDF / pypdf），1 个 PDF 只按文件名匹配", result.stdout)
        self.assertIn("papers/sparse-attention.pdf [pdf, 仅文件名", result.stdout)

    def test_corrupted_pdf_falls_back_gracefully(self):
        write_files(self.kb, {"papers/sparse-attention.pdf": b"%PDF-1.4 not really a pdf"})
        result = self.run_search("sparse attention")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("papers/sparse-attention.pdf [pdf, 仅文件名", result.stdout)

    def test_pdf_with_python_fallback_reports_pages(self):
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                self.skipTest("PyMuPDF (fitz) 未安装")
        pdf_path = self.kb / "papers" / "fallback.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        p1 = doc.new_page()
        p1.insert_text((72, 72), "Introduction overview")
        p2 = doc.new_page()
        p2.insert_text((72, 72), "FlashAttention reduces memory overhead")
        doc.save(pdf_path)
        doc.close()
        result = self.run_search("FlashAttention memory overhead")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_paths(result.stdout)[0], "papers/fallback.pdf")
        self.assertIn("第 2 页", result.stdout)
        self.assertIn("FlashAttention reduces memory overhead", result.stdout)

    def test_pdf_with_pdftotext_reports_pages(self):
        write_files(self.kb, {"papers/report.pdf": b"%PDF-1.4"})
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "pdftotext"
        fake.write_text("#!/bin/sh\nprintf 'Introduction\\n\\fSparse attention kernels reduce memory\\n'\n", encoding="utf-8")
        fake.chmod(0o755)
        result = self.run_search("sparse attention kernels", path=bin_dir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.result_paths(result.stdout)[0], "papers/report.pdf")
        self.assertIn("第 2 页", result.stdout)
        self.assertIn("Sparse attention kernels reduce memory", result.stdout)

    def test_invalid_input_exits_2_without_traceback(self):
        cases = [
            ("pytorch", "--root", str(self.base / "missing")),
            ("？？？",),
            ("pytorch", "--min-coverage", "0"),
            ("pytorch", "--min-coverage", "1.5"),
        ]
        for case in cases:
            result = self.run_search(*case)
            self.assertEqual(result.returncode, 2, case)
            self.assertIn("错误", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
