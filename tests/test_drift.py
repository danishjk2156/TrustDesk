"""Drift watcher tests: change a scratch copy of acmecloud/ and watch approved answers decay.

    python -m unittest discover -s tests -p "test_drift.py" -v
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import drift_watch  # noqa: E402

LIBRARY = yaml.safe_load((ROOT / "room-docs" / "answer-library.yaml").read_text(encoding="utf-8"))["answers"]


def entry(control):
    return next(e for e in LIBRARY if e["control"] == control)


class Drift(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "acmecloud"  # same folder name, so citation paths still line up
        shutil.copytree(ROOT / "acmecloud", self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, rel, old, new):
        path = self.repo / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def by_control(self, report):
        return {s["control"]: s for s in report["stale"] + report["moved"]}

    def test_unchanged_code_is_all_ok(self):
        report = drift_watch.watch(LIBRARY, self.repo)
        self.assertEqual(report["counts"], {"OK": 30, "MOVED": 0, "STALE": 0})

    def test_turning_encryption_off_makes_the_answer_stale(self):
        """The demo beat: someone pushes storage_encrypted = false."""
        self.edit("terraform/rds.tf", "storage_encrypted = true", "storage_encrypted = false")
        report = drift_watch.watch(LIBRARY, self.repo)
        stale = self.by_control(report)
        self.assertEqual(report["counts"]["STALE"], 1)
        self.assertEqual(stale["db_encryption_at_rest"]["status"], "STALE")
        self.assertEqual(stale["db_encryption_at_rest"]["now"], "missing")

    def test_a_new_line_above_only_moves_citations(self):
        self.edit("terraform/rds.tf", 'resource "aws_db_parameter_group"', '# tuned for prod\nresource "aws_db_parameter_group"')
        report = drift_watch.watch(LIBRARY, self.repo)
        self.assertEqual(report["counts"]["STALE"], 0)
        moved = self.by_control(report)
        self.assertEqual(moved["db_encryption_at_rest"]["now_evidence"], ["acmecloud/terraform/rds.tf:19  storage_encrypted = true"])

    def test_changed_value_on_the_cited_line_is_stale(self):
        """Password length drops from 14 to 8: the rule no longer matches, the claim is gone."""
        self.edit("terraform/iam.tf", "minimum_password_length        = 14", "minimum_password_length        = 8")
        stale = self.by_control(drift_watch.watch(LIBRARY, self.repo))
        self.assertEqual(stale["password_length"]["status"], "STALE")

    def test_proof_rewritten_differently_is_stale_not_moved(self):
        """Retention changes from 365 to 400 days: still a pass, but not what was approved."""
        self.edit("terraform/logging.tf", "retention_in_days = 365\n  kms_key_id", "retention_in_days = 400\n  kms_key_id")
        stale = self.by_control(drift_watch.watch(LIBRARY, self.repo))
        self.assertEqual(stale["log_retention"]["status"], "STALE")
        self.assertIn("no longer matches what was approved", stale["log_retention"]["reason"])

    def test_room_summary_names_the_stale_answer(self):
        self.edit("terraform/rds.tf", "storage_encrypted = true", "storage_encrypted = false")
        text = drift_watch.markdown(drift_watch.watch(LIBRARY, self.repo))
        self.assertIn("🔴 1 STALE", text)
        self.assertIn("encrypted at rest", text)

    def test_cli_exit_codes(self):
        script = str(ROOT / "tools" / "drift_watch.py")
        ok = subprocess.run([sys.executable, script, "--repo", str(self.repo)], capture_output=True)
        self.assertEqual(ok.returncode, 0)
        self.edit("terraform/rds.tf", "storage_encrypted = true", "storage_encrypted = false")
        stale = subprocess.run([sys.executable, script, "--repo", str(self.repo), "--markdown"],
                               capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("STALE", stale.stdout)


if __name__ == "__main__":
    unittest.main()
