"""Run the end-to-end sync scenarios in scripts/test_sync.sh against templates/sync.sh.

The scenarios simulate both machines on one host (local mode, no ssh). They rely on GNU tools
(find -printf, touch -d @epoch, truncate) and GNU rsync 3.x, so the test is skipped elsewhere.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL / "templates" / "sync.sh"
SCENARIOS = SKILL / "scripts" / "test_sync.sh"


def gnu_rsync_major():
    if not shutil.which("rsync"):
        return 0
    first = subprocess.run(["rsync", "--version"], capture_output=True, text=True).stdout.splitlines()[:1]
    match = re.match(r"rsync\s+version\s+(\d+)", first[0]) if first else None
    return int(match.group(1)) if match else 0


@unittest.skipUnless(sys.platform.startswith("linux"), "scenarios need GNU coreutils/findutils")
@unittest.skipUnless(shutil.which("bash"), "bash is required")
@unittest.skipUnless(gnu_rsync_major() >= 3, "GNU rsync 3.x is required")
class SyncTemplateScenarioTests(unittest.TestCase):
    def test_all_scenarios_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", str(SCENARIOS), str(TEMPLATE), tmp], capture_output=True, text=True, timeout=300
            )
        report = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, report)
        self.assertNotIn("FAIL", report)
        self.assertRegex(report, r"结果：\d+ 通过，0 失败")

    def test_template_refuses_to_run_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("sanity\n")
            (root / "temp" / "sync").mkdir(parents=True)
            script = root / "temp" / "sync" / "sync.sh"
            shutil.copy(TEMPLATE, script)
            result = subprocess.run(["bash", str(script), "status"], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CHANGE_ME", TEMPLATE.read_text(encoding="utf-8"))
        self.assertIn("还没配置", result.stderr)


if __name__ == "__main__":
    unittest.main()
