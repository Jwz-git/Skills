import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
NAMESPACES = ["论文", "方法", "任务", "模态"]


class AuditTagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.write_profile()

    def tearDown(self):
        self.tmp.cleanup()

    def write_profile(self, strategy="recommended", reject_aliases=False):
        tags = {"strategy": strategy, "allowed_namespaces": NAMESPACES if strategy != "preserve" else [],
                "reject_aliases": reject_aliases}
        (self.root / ".paper-memory.json").write_text(json.dumps({
            "version": 1,
            "memory": {"provider": "obsidian", "location": "."},
            "papers": [{"provider": "filesystem", "location": "/pdfs"}],
            "tags": tags,
        }, ensure_ascii=False), encoding="utf-8")

    def note(self, name, frontmatter):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}---\n\n# 正文\n", encoding="utf-8")
        return path

    def audit(self, *extra):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit_tags.py"), str(self.root), "--json", *extra],
            capture_output=True, text=True, env=ENV,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_zero_indent_block_list_is_audited(self):
        self.note("CLIP.md", "title: CLIP\ntags:\n- 论文/视觉语言\n- paper/vision-language\n- 论文/视觉语言\ndate: 2021\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(report["tag_occurrences"], 3)
        errors = "\n".join(report["errors"])
        self.assertIn("重复标签：论文/视觉语言", errors)
        self.assertIn("命名空间不在 profile 允许范围内：paper/vision-language", errors)

    def test_flow_lists_quotes_comments_and_deep_indent(self):
        self.note("a.md", "tags: [\"方法/对比学习\", '任务/检索'] # 行尾注释\n")
        self.note("b.md", "tags: [论文/视觉语言,\n  模态/图文]\naliases: [CLIP]\n")
        self.note("c.md", "tags: # 注释\n    # 整行注释\n    - 论文/三维视觉   # 领域\n\n    - \"任务/定位\"\n    - '方法/对比学习'\n")
        self.note("d.md", "tags: []\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["files_with_tags"], 4)
        self.assertEqual(report["tag_occurrences"], 7)
        self.assertEqual(report["alias_fields"], 1)

    def test_tag_format_anomalies_are_errors(self):
        cases = {
            "scalar.md": ("tags: 论文/视觉语言\n", "当前为标量"),
            "block-scalar.md": ("tags: |\n  论文/视觉语言\n", "多行字符串"),
            "empty-item.md": ("tags:\n  -\n  - 论文/视觉语言\n", "空标签项"),
            "duplicate-key.md": ("tags: [论文/a]\ntags: [论文/b]\n", "tags 字段重复"),
            "mapping-item.md": ("tags:\n  - 论文: 视觉语言\n", "不能是映射"),
            "unclosed-flow.md": ("tags: [论文/a, 方法/b\ntitle: x\n", "未闭合"),
            "empty-leaf.md": ("tags: [论文/]\n", "缺少叶节点"),
        }
        for name, (frontmatter, _) in cases.items():
            self.note(name, frontmatter)
        (self.root / "unclosed.md").write_text("---\ntags: [论文/a]\n# 没有结束标记\n", encoding="utf-8")
        code, report = self.audit()
        self.assertEqual(code, 1)
        errors = report["errors"]
        for name, (_, message) in cases.items():
            self.assertTrue(any(error.startswith(name) and message in error for error in errors), (name, errors))
        self.assertTrue(any(error.startswith("unclosed.md") and "未闭合" in error for error in errors))

    def test_preserve_strategy_keeps_existing_taxonomy(self):
        self.write_profile(strategy="preserve")
        self.note("a.md", "tags: [CLIP, vision-language, paper/vlm]\naliases: [Contrastive]\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report["errors"])

    def test_reject_aliases_and_legacy_tag_key(self):
        self.write_profile(reject_aliases=True)
        self.note("a.md", "tags: [论文/视觉语言]\naliases:\n  - CLIP\n")
        self.note("b.md", "tag: 论文/视觉语言\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertTrue(any("profile 禁止使用 aliases" in error for error in report["errors"]))
        self.assertTrue(any(warning.startswith("b.md") and "旧字段 tag" in warning for warning in report["warnings"]))

    def test_cross_namespace_leaf_is_error_only_when_strict(self):
        self.note("a.md", "tags: [论文/定位]\n")
        self.note("b.md", "tags: [任务/定位]\n")
        code, report = self.audit()
        self.assertEqual(code, 0)
        self.assertIn("定位", report["cross_namespace_leafs"])
        code, _ = self.audit("--strict-cross-namespace")
        self.assertEqual(code, 1)

    def test_hidden_directories_are_skipped(self):
        self.note(".trash/old.md", "tags: oops\n")
        self.note("a.md", "tags: [论文/视觉语言]\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report["errors"])
        self.assertEqual(report["markdown_files"], 1)


if __name__ == "__main__":
    unittest.main()
