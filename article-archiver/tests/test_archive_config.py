import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archive_config.py"


class ArchiveConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, xdg=None):
        env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        env.pop("XDG_CONFIG_HOME", None)
        if xdg is not None:
            env["XDG_CONFIG_HOME"] = str(xdg)
        return subprocess.run(
            ["python3", str(SCRIPT), *args], text=True, capture_output=True, env=env
        )

    def run_json(self, *args, expected_code=0, **kwargs):
        result = self.run_script(*args, **kwargs)
        self.assertEqual(result.returncode, expected_code, result.stderr)
        return json.loads(result.stdout)

    def assert_clean_error(self, result, message):
        self.assertEqual(result.returncode, 2)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def default_personal(self):
        return self.home / ".config" / "article-archiver" / "config.json"

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_personal_config_path_follows_xdg(self):
        report = self.run_json("resolve", "--cwd", str(self.project))
        self.assertEqual(report["personal_config"], {"path": str(self.default_personal()), "exists": False})
        self.assertFalse(report["configured"])
        self.assertIsNone(report["project_config"])

        xdg = self.root / "xdg"
        report = self.run_json("resolve", "--cwd", str(self.project), xdg=xdg)
        self.assertEqual(report["personal_config"]["path"], str(xdg / "article-archiver" / "config.json"))

        for ignored in ("", "relative/dir"):
            report = self.run_json("resolve", "--cwd", str(self.project), xdg=ignored)
            self.assertEqual(report["personal_config"]["path"], str(self.default_personal()))

    def test_project_overrides_personal_recursively(self):
        nested = self.project / "nested"
        nested.mkdir()
        self.write(self.default_personal(), {"assets": {"mode": "remote"}, "output_dir": "personal"})
        self.write(self.project / ".article-archiver.json", {"output_dir": "project"})
        report = self.run_json("resolve", "--cwd", str(nested))
        self.assertEqual(report["config"]["output_dir"], "project")
        self.assertEqual(report["config"]["assets"], {"mode": "remote", "directory": "assets"})
        self.assertTrue(report["configured"])
        self.assertTrue(report["personal_config"]["exists"])
        self.assertEqual(report["project_config"], str(self.project / ".article-archiver.json"))

    def test_init_marks_configured_and_refuses_overwrite(self):
        self.run_script("init", "--scope", "project", "--cwd", str(self.project))
        self.assertTrue(self.run_json("resolve", "--cwd", str(self.project))["configured"])
        result = self.run_script("init", "--scope", "project", "--cwd", str(self.project))
        self.assert_clean_error(result, "拒绝替换已有配置")

    def test_init_personal_writes_xdg_path(self):
        result = self.run_script("init", "--scope", "personal", "--assets", "omit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.default_personal().read_text(encoding="utf-8"))["assets"]["mode"], "omit")

    def test_set_rejects_unknown_keys_and_bad_values(self):
        config = self.project / ".article-archiver.json"
        cases = [
            (("assets", "remote"), "不支持的配置键"),
            (("version", "2"), "不支持的配置键"),
            (("assets.mode", "foo"), "不支持的 assets.mode"),
            (("output_dir", " "), "output_dir 必须是非空字符串"),
        ]
        for (key, value), message in cases:
            result = self.run_script("set", "--scope", "project", "--cwd", str(self.project), key, value)
            self.assert_clean_error(result, message)
            self.assertFalse(config.exists())

    def test_set_keeps_values_as_strings_and_updates_nearest_config(self):
        nested = self.project / "nested"
        nested.mkdir()
        config = self.project / ".article-archiver.json"
        self.write(config, {"output_dir": "notes"})
        result = self.run_script("set", "--scope", "project", "--cwd", str(nested), "assets.directory", "2024")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"output_dir": "notes", "assets": {"directory": "2024"}})
        self.assertFalse((nested / ".article-archiver.json").exists())

    def test_bad_types_in_config_files_are_clean_errors(self):
        config = self.project / ".article-archiver.json"
        for value, message in (
            ({"assets": "remote"}, "assets 必须是对象"),
            ({"assets": {"mode": ["download"]}}, "不支持的 assets.mode"),
            ({"output_dir": 5}, "output_dir 必须是非空字符串"),
            ([], "配置根节点必须是对象"),
        ):
            self.write(config, value)
            self.assert_clean_error(self.run_script("resolve", "--cwd", str(self.project)), message)
            self.assert_clean_error(self.run_script("plan", "--cwd", str(self.project), "--name", "x"), message)
        self.write(config, {"assets": "remote"})
        result = self.run_script("set", "--scope", "project", "--cwd", str(self.project), "assets.mode", "omit")
        self.assert_clean_error(result, "assets 必须是对象")

    def test_default_plan_layout(self):
        result = self.run_json("plan", "--cwd", str(self.project), "--name", "原始标题")
        self.assertEqual(result["markdown"], str(self.project / "articles" / "原始标题.md"))
        self.assertEqual(result["assets_dir"], str(self.project / "articles" / "assets" / "原始标题"))
        self.assertEqual(result["conflicts"], [])
        self.assertFalse(result["truncated"])

    def test_plan_uses_configured_assets(self):
        self.run_script("init", "--scope", "project", "--cwd", str(self.project), "--assets-directory", "media")
        result = self.run_json("plan", "--cwd", str(self.project), "--name", "T")
        self.assertEqual(result["assets_dir"], str(self.project / "articles" / "media" / "T"))
        self.run_script("set", "--scope", "project", "--cwd", str(self.project), "assets.mode", "remote")
        self.assertIsNone(self.run_json("plan", "--cwd", str(self.project), "--name", "T")["assets_dir"])

    def test_plan_sanitizes_and_detects_conflict(self):
        first = self.run_json("plan", "--cwd", str(self.project), "--name", "Bad: title.md")
        article = Path(first["markdown"])
        self.assertEqual(article.name, "Bad- title.md")
        article.parent.mkdir(parents=True)
        article.write_text("occupied", encoding="utf-8")
        second = self.run_json("plan", "--cwd", str(self.project), "--name", "Bad: title.md", expected_code=1)
        self.assertEqual(second["conflicts"], [str(article)])
        self.assertEqual(self.run_json("plan", "--cwd", str(self.project), "--name", " .hidden\ttitle. ")["name"], "hidden title")

    def test_plan_truncates_long_names_by_utf8_bytes(self):
        for title in ("中" * 100, "a" + "中" * 100):
            result = self.run_json("plan", "--cwd", str(self.project), "--name", title)
            name = result["name"]
            self.assertTrue(result["truncated"])
            self.assertTrue(title.startswith(name))
            self.assertLessEqual(len(name.encode("utf-8")), 200)
            self.assertGreater(len(name.encode("utf-8")), 196)
            self.assertLessEqual(len(Path(result["markdown"]).name.encode("utf-8")), 255)

    def test_plan_rejects_names_without_usable_characters(self):
        for name in ("", "  ", "...", ".md"):
            result = self.run_script("plan", "--cwd", str(self.project), "--name", name)
            self.assert_clean_error(result, "名称无法生成有效文件名")

    def test_output_dir_base(self):
        nested = self.project / "nested"
        nested.mkdir()
        self.write(self.project / ".article-archiver.json", {"output_dir": "articles"})
        result = self.run_json("plan", "--cwd", str(nested), "--name", "T")
        self.assertEqual(result["markdown"], str(self.project / "articles" / "T.md"))

        self.write(self.default_personal(), {"output_dir": "~/notes"})
        (self.project / ".article-archiver.json").unlink()
        result = self.run_json("plan", "--cwd", str(nested), "--name", "T")
        self.assertEqual(result["markdown"], str(self.home / "notes" / "T.md"))


if __name__ == "__main__":
    unittest.main()
