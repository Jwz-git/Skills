import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class AuditImageAttachmentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name).resolve() / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.memory = self.vault / "papers"
        self.memory.mkdir()
        self.write_profile()

    def tearDown(self):
        self.tmp.cleanup()

    def write_profile(self, provider="obsidian", link_style="markdown", pattern="./assets/${noteFileName}"):
        (self.memory / ".paper-memory.json").write_text(json.dumps({
            "version": 1,
            "memory": {"provider": provider, "location": "."},
            "papers": [{"provider": "filesystem", "location": "/pdfs"}],
            "attachments": {"mode": "local", "root_pattern": pattern, "link_style": link_style},
        }), encoding="utf-8")

    def file(self, relative, content="", base=None):
        path = (base or self.memory) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".md":
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(b"image")
        return path

    def audit(self, *extra):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit_image_attachments.py"), str(self.memory), "--json", *extra],
            capture_output=True, text=True, env=ENV,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout)

    @staticmethod
    def targets(entries):
        return sorted(entry["target"] for entry in entries)

    def test_explicit_wrong_path_is_broken_without_basename_guess(self):
        self.file("assets/CLIP/fig.png")
        self.file("CLIP.md", "![总体流程](./assets/WRONG/fig.png)\n![流程](assets/WRONG/fig.png)\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(self.targets(report["broken_references"]), ["./assets/WRONG/fig.png", "assets/WRONG/fig.png"])
        self.assertEqual(report["ambiguous_references"], [])
        self.assertEqual(report["unreferenced_images"], ["papers/assets/CLIP/fig.png"])

    def test_encoded_angle_bracket_title_and_fragment_forms_resolve(self):
        self.file("assets/My Note/fig 1.png")
        self.file("other/fig 1.png")
        self.file("My Note.md", "\n".join([
            '![编码](./assets/My%20Note/fig%201.png "图 1")',
            "![尖括号](<./assets/My Note/fig 1.png>)",
            "![片段](./assets/My%20Note/fig%201.png#page=2)",
            "![查询](assets/My%20Note/fig%201.png?raw=1)",
            '<img src="./assets/My%20Note/fig%201.png" alt="html">',
        ]) + "\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["image_references"], 5)
        self.assertEqual(report["unique_referenced_images"], 1)
        self.assertEqual(report["unreferenced_images"], ["papers/other/fig 1.png"])

    def test_external_urls_and_code_are_ignored(self):
        self.file("CLIP.md", "\n".join([
            "![远程](https://example.com/fig.png)",
            "![内联](data:image/png;base64,AAAA)",
            "![协议相对](//cdn.example.com/a.png)",
            "```markdown",
            "![示例](./assets/missing.png)",
            "```",
            "说明 `![行内](./assets/missing.png)` 语法。",
        ]) + "\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["image_references"], 0)

    def test_obsidian_bare_names_prefer_same_folder_then_unique_name(self):
        self.write_profile(link_style="wiki", pattern="./")
        self.file("diagram.png")
        self.file("assets/elsewhere/diagram.png")
        self.file("assets/CLIP/unique.png")
        self.file("a/dup.png")
        self.file("b/dup.png")
        self.file("CLIP.md", "![[diagram.png]]\n![[unique.png|300]]\n![](unique.png)\n![[dup.png]]\n")
        _, report = self.audit()
        self.assertEqual(report["broken_references"], [])
        self.assertEqual(self.targets(report["ambiguous_references"]), ["dup.png"])
        self.assertEqual(
            sorted(report["ambiguous_references"][0]["candidates"]), ["papers/a/dup.png", "papers/b/dup.png"]
        )
        self.assertIn("papers/assets/elsewhere/diagram.png", report["unreferenced_images"])
        self.assertNotIn("papers/assets/CLIP/unique.png", report["unreferenced_images"])

    def test_markdown_provider_treats_bare_names_as_note_relative(self):
        self.write_profile(provider="markdown")
        self.file("assets/CLIP/fig.png")
        self.file("CLIP.md", "![图](fig.png)\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(self.targets(report["broken_references"]), ["fig.png"])
        self.assertIsNone(report["vault_root"])

    def test_vault_root_relative_path_and_outside_memory_reference(self):
        self.file("assets/CLIP/fig.png")
        outside = self.file("imgs/legacy.png", base=self.vault)
        self.file("CLIP.md", "![库根路径](papers/assets/CLIP/fig.png)\n![旧图](../imgs/legacy.png)\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(report["broken_references"], [])
        self.assertEqual(report["vault_root"], str(self.vault))
        self.assertEqual(
            report["misplaced_images"],
            [{"image": "imgs/legacy.png", "note": "papers/CLIP.md", "expected_parent": "papers/assets/CLIP"}],
        )
        self.assertTrue(outside.exists())

    def test_case_mismatch_is_reported(self):
        self.file("assets/CLIP/fig.png")
        self.file("CLIP.md", "![大小写](./assets/CLIP/FIG.png)\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(self.targets(report["broken_references"]), ["./assets/CLIP/FIG.png"])

    def test_scope_limits_reporting_but_not_reference_collection(self):
        self.file("A/assets/x/used-by-b.png")
        self.file("A/x.md", "无图\n")
        self.file("B/other.md", "![跨目录](../A/assets/x/used-by-b.png)\n![断链](./missing.png)\n")
        code, report = self.audit("--scope", "A")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["notes_scanned"], 1)
        self.assertEqual(report["unreferenced_images"], [])
        self.assertEqual(report["broken_references"], [])
        self.assertEqual(report["misplaced_images"], [])

        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(self.targets(report["broken_references"]), ["./missing.png"])
        self.assertEqual([item["image"] for item in report["misplaced_images"]], ["papers/A/assets/x/used-by-b.png"])

    def test_wiki_embed_conflicts_only_with_markdown_link_style(self):
        self.file("assets/CLIP/fig.png")
        self.file("CLIP.md", "![[assets/CLIP/fig.png]]\n")
        code, report = self.audit()
        self.assertEqual(code, 1)
        self.assertEqual(self.targets(report["wiki_embeds"]), ["assets/CLIP/fig.png"])
        self.write_profile(link_style="wiki")
        code, report = self.audit()
        self.assertEqual(code, 0, report)

    def test_shared_images_are_reported_not_misplaced(self):
        self.file("assets/common/shared.png")
        self.file("A.md", "![共享](./assets/common/shared.png)\n")
        self.file("B.md", "![共享](./assets/common/shared.png)\n")
        code, report = self.audit()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["shared_images"], [{"image": "papers/assets/common/shared.png",
                                                    "notes": ["papers/A.md", "papers/B.md"]}])


if __name__ == "__main__":
    unittest.main()
