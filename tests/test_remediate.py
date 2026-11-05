"""Remediator tests: a known gap gets a code fix that the evidence engine then proves.

Runs on scratch copies of acmecloud/; the real repo keeps its seeded gaps for the demo.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import remediate  # noqa: E402
import search  # noqa: E402


class Remediate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "acmecloud"
        shutil.copytree(ROOT / "acmecloud", self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exports_bucket_gets_encrypted_and_proven(self):
        self.assertEqual(search.check("storage_encryption_all_buckets", self.repo)["status"], "contradicted")
        report, code = remediate.remediate("storage_encryption_all_buckets", self.repo)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "proven")
        self.assertIn("exports", report["changes"][0])
        self.assertEqual(search.check("storage_encryption_all_buckets", self.repo)["status"], "found")

    def test_mfa_for_all_users_gets_enforced_and_proven(self):
        report, code = remediate.remediate("mfa_all_users", self.repo)
        self.assertEqual((code, report["status"]), (0, "proven"))
        self.assertIn("enforced_for_all_users: true", (self.repo / "configs/auth.yaml").read_text(encoding="utf-8"))

    def test_fix_does_not_break_other_proofs(self):
        before = {c: search.check(c, self.repo)["status"] for c in search.RULES}
        remediate.remediate("storage_encryption_all_buckets", self.repo)
        remediate.remediate("mfa_all_users", self.repo)
        after = {c: search.check(c, self.repo)["status"] for c in search.RULES}
        regressions = [c for c in before if before[c] == "found" and after[c] != "found"]
        self.assertEqual(regressions, [])

    def test_dry_run_changes_nothing(self):
        s3 = (self.repo / "terraform/s3.tf").read_text(encoding="utf-8")
        report, code = remediate.remediate("storage_encryption_all_buckets", self.repo, dry=True)
        self.assertEqual((code, report["status"]), (0, "dry-run"))
        self.assertEqual((self.repo / "terraform/s3.tf").read_text(encoding="utf-8"), s3)

    def test_second_run_has_nothing_to_fix(self):
        remediate.remediate("mfa_all_users", self.repo)
        report, code = remediate.remediate("mfa_all_users", self.repo)
        self.assertEqual((code, report["status"]), (3, "nothing-to-fix"))

    def test_people_gaps_have_no_code_recipe(self):
        report, code = remediate.remediate("background_checks", self.repo)
        self.assertEqual((code, report["status"]), (3, "no-recipe"))

    def test_real_repo_keeps_its_seeded_gaps(self):
        self.assertEqual(search.check("storage_encryption_all_buckets")["status"], "contradicted")
        self.assertEqual(search.check("mfa_all_users")["status"], "contradicted")


if __name__ == "__main__":
    unittest.main()
