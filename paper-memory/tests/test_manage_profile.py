import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "manage_profile.py"), *map(str, args)],
        capture_output=True, text=True, env=ENV,
    )


def init(output, *extra):
    return run(
        "init", "--output", output, "--memory-provider", "obsidian", "--memory-location", output,
        "--paper-source", "filesystem=/library/pdfs", *extra,
    )


class ManageProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.profile = self.root / ".paper-memory.json"

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        return json.loads(self.profile.read_text(encoding="utf-8"))

    def test_init_sets_index_and_metadata_fields(self):
        result = init(
            self.root, "--link-style", "wiki", "--backlinks", "suggest", "--index", "MOCs",
            "--metadata-field", "Title", "--metadata-field", "Year", "--metadata-field", "Tags",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.read()
        self.assertEqual(data["links"], {"style": "wiki", "backlinks": "suggest", "index": "MOCs"})
        self.assertEqual(data["metadata"]["fields"], ["Title", "Year", "Tags"])
        self.assertEqual(run("validate", self.root).returncode, 0)

    def test_init_defaults_and_refuses_overwrite(self):
        self.assertEqual(init(self.root).returncode, 0)
        data = self.read()
        self.assertIsNone(data["links"]["index"])
        self.assertEqual(data["metadata"]["fields"], ["title", "date", "tags"])
        before = self.profile.read_text(encoding="utf-8")
        again = init(self.root, "--link-style", "wiki")
        self.assertEqual(again.returncode, 1)
        self.assertIn("拒绝覆盖", again.stderr)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), before)

    def test_init_rejects_invalid_combinations(self):
        result = init(self.root, "--review-persistence", "external")
        self.assertEqual(result.returncode, 1)
        self.assertIn("review.location", result.stderr)
        self.assertFalse(self.profile.exists())

        note = self.root / "CLIP.md"
        note.write_text("# CLIP\n", encoding="utf-8")
        result = init(note)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--output", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.profile.exists())

    def test_show_accepts_note_directory_profile_and_future_note(self):
        self.assertEqual(init(self.root).returncode, 0)
        note = self.root / "papers" / "CLIP.md"
        note.parent.mkdir()
        note.write_text("# CLIP\n", encoding="utf-8")
        for target in (note, note.parent, self.root, self.profile, note.parent / "New.md"):
            result = run("show", target)
            self.assertEqual(result.returncode, 0, f"{target}: {result.stderr}")
            report = json.loads(result.stdout)
            self.assertEqual(report["profile_path"], str(self.profile))
            self.assertEqual(report["memory_root"], str(self.root))

    def test_show_reports_missing_profile(self):
        empty = self.root / "empty"
        empty.mkdir()
        result = run("show", empty)
        self.assertEqual(result.returncode, 3)
        self.assertIn(".paper-memory.json", result.stderr)

    def test_set_validates_before_atomic_write(self):
        self.assertEqual(init(self.root).returncode, 0)
        note = self.root / "CLIP.md"
        note.write_text("# CLIP\n", encoding="utf-8")

        self.assertEqual(run("set", note, "links.backlinks", "maintain").returncode, 0)
        self.assertEqual(run("set", self.root, "metadata.fields", '["Title", "Year"]').returncode, 0)
        self.assertEqual(run("set", self.root, "links.index", "MOCs/").returncode, 0)
        data = self.read()
        self.assertEqual(data["links"]["backlinks"], "maintain")
        self.assertEqual(data["metadata"]["fields"], ["Title", "Year"])
        self.assertEqual(data["links"]["index"], "MOCs/")

        before = self.profile.read_text(encoding="utf-8")
        for key, value in (
            ("links.style", "bogus"),
            ("links.styel", "wiki"),
            ("version", "2"),
            ("review.persistence", "external"),
            ("metadata.fields", "Title"),
        ):
            result = run("set", self.root, key, value)
            self.assertEqual(result.returncode, 1, f"{key}={value}")
            self.assertEqual(self.profile.read_text(encoding="utf-8"), before)

        self.assertEqual(run("set", self.root, "review.location", "复习记录").returncode, 0)
        self.assertEqual(run("set", self.root, "review.persistence", "external").returncode, 0)
        self.assertEqual(run("set", self.root, "links.index", "null").returncode, 0)
        data = self.read()
        self.assertEqual(data["review"], {"persistence": "external", "location": "复习记录"})
        self.assertIsNone(data["links"]["index"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".paper-memory.json", "CLIP.md"])

    def test_minimal_profile_gets_defaults(self):
        self.profile.write_text(json.dumps({
            "version": 1,
            "memory": {"provider": "markdown", "location": "."},
            "papers": [{"provider": "filesystem", "location": "/pdfs"}],
        }), encoding="utf-8")
        result = run("show", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        profile = json.loads(result.stdout)["profile"]
        self.assertEqual(profile["links"]["style"], "none")
        self.assertEqual(profile["tags"]["strategy"], "preserve")

    def test_validate_rejects_yaml_and_unknown_fields(self):
        self.profile.write_text("version: 1\nmemory:\n  provider: obsidian\n", encoding="utf-8")
        result = run("validate", self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON", result.stderr)

        self.profile.write_text(json.dumps({
            "version": 1,
            "memory": {"provider": "obsidian", "location": "."},
            "papers": [{"provider": "filesystem", "location": "/pdfs"}],
            "link": {"style": "wiki"},
            "attachments": {"mode": "local", "root_pattern": "./assets/${noteTitle}", "link_style": "markdown"},
        }), encoding="utf-8")
        result = run("validate", self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("未知字段：link", result.stderr)
        self.assertIn("只支持变量", result.stderr)


if __name__ == "__main__":
    unittest.main()
