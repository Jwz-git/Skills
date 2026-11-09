import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "frontmatter_audit.py"
EVALS = SKILL / "evals"

spec = importlib.util.spec_from_file_location("frontmatter_audit", SCRIPT)
fa = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(fa)


def analyze(text: str, **kwargs):
    return fa.analyze_bytes(text.encode("utf-8"), **kwargs)


def codes(record):
    return [issue["code"] for issue in record["issues"]]


def run(*args: str):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, env=env)
    stdout = result.stdout.decode("utf-8")
    return result.returncode, (json.loads(stdout) if stdout.strip() else None), result.stderr.decode("utf-8")


class ParsingTests(unittest.TestCase):
    def test_zero_indent_block_list(self):
        record = analyze("---\ntags:\n- a\n- b\ntitle: x\n---\nbody\n")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["properties"]["tags"], {"kind": "block", "line": 2, "indent": 0, "values": ["a", "b"]})
        self.assertEqual([item["key"] for item in record["keys"]], ["tags", "title"])
        self.assertTrue(record["editable"])

    def test_indented_block_list_with_quotes_and_comments(self):
        record = analyze("---\ntags:\n    - 'it''s'\n    # note\n    - \"a\\u0062\"  # trailing\n    - c\n---\n")
        self.assertEqual(record["properties"]["tags"]["indent"], 4)
        self.assertEqual(record["properties"]["tags"]["values"], ["it's", "ab", "c"])

    def test_flow_lists_single_and_multi_line(self):
        record = analyze('---\ntags: [Foo, "two words", c#]\naliases: [\n  one,\n  "two"\n]\n---\n')
        self.assertEqual(record["properties"]["tags"]["values"], ["Foo", "two words", "c#"])
        self.assertEqual(record["properties"]["aliases"], {"kind": "flow", "line": 3, "values": ["one", "two"]})
        self.assertIn("tag_whitespace", codes(record))
        self.assertIn("tag_unlisted_chars", codes(record))

    def test_scalar_and_empty_list_properties(self):
        record = analyze("---\naliases: K8s 部署\ncssclasses:\ntags: []\n---\n")
        self.assertEqual(record["properties"]["aliases"], {"kind": "scalar", "line": 2, "value": "K8s 部署"})
        self.assertEqual(record["properties"]["cssclasses"]["kind"], "empty")
        self.assertEqual(record["properties"]["tags"]["values"], [])
        self.assertEqual(codes(record), ["scalar_list_property", "empty_property"])

    def test_unquoted_hash_item_is_comment(self):
        record = analyze("---\ntags:\n  - #foo\n  - bar\n---\n")
        self.assertEqual(record["properties"]["tags"]["values"], [None, "bar"])
        issue = next(issue for issue in record["issues"] if issue["code"] == "empty_item")
        self.assertIn("注释", issue["message"])

    def test_tag_rules_and_case_duplicates(self):
        record = analyze("---\ntags: [Foo, foo, 1234, Foo, valid/tag]\n---\n")
        self.assertEqual(sorted(codes(record)), ["case_duplicate_item", "duplicate_item", "tag_numeric"])

    def test_duplicate_keys_including_quoted_spelling(self):
        record = analyze('---\ntitle: a\n"title": b\n---\n')
        issue = next(issue for issue in record["issues"] if issue["code"] == "duplicate_key")
        self.assertEqual(issue["lines"], [2, 3])
        self.assertFalse(record["editable"])

    def test_legacy_keys_and_collision(self):
        record = analyze("---\ncssclass: wide\ncssclasses: [wide]\nTags: [a]\n---\n")
        self.assertEqual(codes(record), ["legacy_key", "legacy_collision", "noncanonical_key_case"])

    def test_unclosed_and_dot_end(self):
        unclosed = analyze("---\ntitle: x\n\n# Body with `---` inline\n")
        self.assertEqual(unclosed["status"], "unclosed")
        self.assertIsNone(unclosed["body_sha256"])
        dot = analyze("---\ntitle: x\n...\nbody\n")
        self.assertEqual(dot["status"], "unclosed")
        self.assertIn("dot_end_marker", codes(dot))
        accepted = analyze("---\ntitle: x\n...\nbody\n", allow_dot_end=True)
        self.assertEqual(accepted["status"], "ok")
        before_close = analyze("---\ntitle: x\n...\nmore: y\n---\n")
        self.assertEqual(before_close["status"], "invalid")

    def test_no_frontmatter_and_horizontal_rule(self):
        record = analyze("# Title\n\n---\n\ntext\n")
        self.assertEqual(record["status"], "none")
        self.assertEqual(record["body_sha256"], fa.text_sha256("# Title\n\n---\n\ntext\n"))

    def test_bom_and_crlf(self):
        body = "\r\nBody line\r\n"
        data = b"\xef\xbb\xbf" + ("---\r\ntags:\r\n  - a\r\n---\r\n" + body).encode("utf-8")
        record = fa.analyze_bytes(data)
        self.assertTrue(record["bom"])
        self.assertEqual(record["newline"], "crlf")
        self.assertEqual(record["properties"]["tags"]["values"], ["a"])
        self.assertEqual(record["body_sha256"], fa.text_sha256(body))

    def test_block_scalar_content_is_not_scanned(self):
        record = analyze("---\nsummary: |\n  title: fake\n  - item\n  ---x\ntitle: real\nnote: >-\n  tags: [x]\n---\n")
        self.assertEqual(record["status"], "ok")
        self.assertEqual([item["key"] for item in record["keys"]], ["summary", "title", "note"])
        self.assertNotIn("duplicate_key", codes(record))
        self.assertNotIn("tags", record["properties"])

    def test_invalid_yaml_is_reported_not_guessed(self):
        cases = {
            "---\ntitle: Note: Part 1\n---\n": "invalid_plain_scalar",
            "---\ntags:\n\t- a\n---\n": "tab_indentation",
            "---\n- a\n---\n": "not_mapping",
            "---\ntags: [a, b\n---\n": "unterminated_flow",
            "---\ntags:\n  - a\n - b\n---\n": "bad_indentation",
        }
        for text, code in cases.items():
            with self.subTest(code=code):
                record = analyze(text)
                self.assertEqual(record["status"], "invalid")
                self.assertEqual(codes(record), [code])
                self.assertFalse(record["editable"])
        unsupported = analyze("---\n? complex\n: value\n---\n")
        self.assertEqual(unsupported["status"], "unsupported")

    def test_glob_matching(self):
        patterns = [fa.compile_glob("Archive/**"), fa.compile_glob("*.draft.md"), fa.compile_glob("docs/**/*.md")]
        self.assertTrue(fa.matches(patterns[:1], "Archive/a/b.md"))
        self.assertTrue(fa.matches(patterns[1:2], "x/y.draft.md"))
        self.assertTrue(fa.matches(patterns[2:], "docs/a.md"))
        self.assertTrue(fa.matches(patterns[2:], "docs/a/b.md"))
        self.assertFalse(fa.matches(patterns[2:], "other/docs/a.md"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.vault.mkdir()
        self.snapshot = self.temp / "before.json"

    def tearDown(self):
        shutil.rmtree(self.temp)

    def write(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def take_snapshot(self):
        code, report, stderr = run("snapshot", str(self.vault), "--out", str(self.snapshot))
        self.assertEqual(code, 0, stderr)
        return report

    def verify(self, *fields: str):
        args = ["verify", str(self.vault), "--snapshot", str(self.snapshot)]
        for field in fields:
            args += ["--fields", field]
        return run(*args)

    def test_frontmatter_only_change_passes(self):
        note = self.write("a.md", "---\ntitle: A\ntags:\n  - old\n  - keep\n---\nBody #old\n")
        self.take_snapshot()
        note.write_bytes("---\ntitle: A\ntags:\n  - new\n  - keep\n---\nBody #old\n".encode("utf-8"))
        code, report, _ = self.verify("tags")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["changed"][0]["keys_modified"], ["tags"])
        self.assertEqual(report["changed"][0]["lists"]["tags"]["after"]["values"], ["new", "keep"])

    def test_body_change_is_violation(self):
        note = self.write("a.md", "---\ntags: [a]\n---\nBody\n")
        self.take_snapshot()
        note.write_bytes("---\ntags: [b]\n---\nBody edited\n".encode("utf-8"))
        code, report, _ = self.verify("tags")
        self.assertEqual(code, 1)
        self.assertEqual([item["code"] for item in report["violations"]], ["body_changed"])

    def test_scope_order_and_list_invariants(self):
        note = self.write("a.md", "---\ntitle: A\ntags: [a, b]\naliases: [x]\n---\n")
        self.take_snapshot()
        note.write_bytes("---\naliases: [x]\ntitle: B\ntags: [a, a]\n---\n".encode("utf-8"))
        code, report, _ = self.verify("tags")
        self.assertEqual(code, 1)
        found = {item["code"] for item in report["violations"]}
        self.assertEqual(found, {"key_order_changed", "unauthorized_key_change", "duplicate_items"})

    def test_list_property_must_stay_a_list(self):
        note = self.write("a.md", "---\ntags: [a]\n---\n")
        self.take_snapshot()
        note.write_bytes("---\ntags: a\n---\n".encode("utf-8"))
        code, report, _ = self.verify("tags")
        self.assertEqual(code, 1)
        self.assertEqual(report["violations"][0]["code"], "list_property_not_list")

    def test_adding_frontmatter_keeps_original_content(self):
        note = self.write("runbook.md", "# Runbook\n\n```sh\nkubectl apply\n```\n")
        self.take_snapshot()
        note.write_bytes("---\ntags: []\nowner: ops\n---\n# Runbook\n\n```sh\nkubectl apply\n```\n".encode("utf-8"))
        code, report, _ = self.verify("tags,owner")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["changed"][0]["change"], "frontmatter_added")

    def test_file_set_and_unsafe_files(self):
        self.write("broken.md", "---\ntitle: x\n")
        self.write("keep.md", "---\ntitle: k\n---\n")
        self.take_snapshot()
        self.write("broken.md", "---\ntitle: x\n---\n")
        self.write("new.md", "---\ntitle: n\n---\n")
        self.write("keep.md.bak", "backup")
        code, report, _ = self.verify()
        self.assertEqual(code, 1)
        found = {(item["path"], item["code"]) for item in report["violations"]}
        self.assertEqual(found, {
            ("broken.md", "edited_unsafe_file"),
            ("new.md", "file_added"),
            ("keep.md.bak", "other_file_added"),
        })

    def test_discovery_skips_hidden_symlinks_and_excludes(self):
        self.write("a.md", "---\ntitle: a\n---\n")
        self.write(".obsidian/x.md", "---\ntitle: hidden\n---\n")
        self.write("Archive/old.md", "---\ntitle: old\n---\n")
        os.symlink(self.vault / "a.md", self.vault / "link.md")
        code, report, _ = run("audit", str(self.vault), "--exclude", "Archive/**")
        self.assertEqual(code, 0, report)
        self.assertEqual([item["path"] for item in report["files"]], ["a.md"])
        self.assertEqual(report["discovery"], {"hidden": 1, "excluded": 1, "symlinks": ["link.md"]})

    def test_snapshot_must_live_outside_scope(self):
        self.write("a.md", "---\ntitle: a\n---\n")
        code, _, stderr = run("snapshot", str(self.vault), "--out", str(self.vault / "snap.json"))
        self.assertEqual(code, 2)
        self.assertIn("快照不能写进扫描范围", stderr)
        self.assertFalse((self.vault / "snap.json").exists())

    def test_eval_fixtures(self):
        code, report, _ = run("audit", str(EVALS / "files" / "problem-vault"))
        self.assertEqual(code, 1)
        files = {item["path"]: item for item in report["files"]}
        expected = {
            "alias-a.md": ("ok", "legacy_key"),
            "body-only.md": ("none", "no_frontmatter"),
            "dot-end.md": ("unclosed", "dot_end_marker"),
            "duplicate-key.md": ("ok", "duplicate_key"),
            "tag-problems.md": ("ok", "case_duplicate_item"),
            "unclosed.md": ("unclosed", "unclosed"),
        }
        for path, (status, code_name) in expected.items():
            with self.subTest(path=path):
                self.assertEqual(files[path]["status"], status)
                self.assertIn(code_name, [issue["code"] for issue in files[path]["issues"]])
        self.assertEqual(report["inventory"]["shared_aliases"], [{"value": "客户端指南", "files": ["alias-a.md", "alias-b.md"]}])

    def test_golden_files_pass_verify(self):
        shutil.rmtree(self.vault)
        shutil.copytree(EVALS / "files" / "technical-vault", self.vault)
        self.take_snapshot()
        for golden in (EVALS / "expected" / "technical-vault").glob("*.md"):
            shutil.copyfile(golden, self.vault / golden.name)
        code, report, _ = self.verify("tags")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["summary"]["changed"], 2)
        self.take_snapshot_again()

    def take_snapshot_again(self):
        code, _, stderr = run("snapshot", str(self.vault), "--out", str(self.snapshot), "--force")
        self.assertEqual(code, 0, stderr)
        code, report, _ = self.verify("tags")
        self.assertEqual((code, report["summary"]["changed"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
