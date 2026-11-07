"""Release blockers: a bad fix must be rejected and rolled back before a PR is opened.

Each case swaps in a deliberately faulty recipe and checks the independent validator catches it.

    python -m unittest discover -s tests -p "test_adv_remediation.py" -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import remediate  # noqa: E402
import remediation_check  # noqa: E402
import search  # noqa: E402

CONTROL = "storage_encryption_all_buckets"
GOOD = remediate.SSE_BLOCK.format(name="exports")
SCOPE = remediation_check.Scope({"terraform/s3.tf": "append"}, frozenset({"aws_s3_bucket_server_side_encryption_configuration"}))


class BadRecipes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "acmecloud"
        shutil.copytree(ROOT / "acmecloud", self.repo)
        self.before = remediation_check._read_all(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_recipe(self, fix, scope=SCOPE, **kw):
        with mock.patch.dict(remediate.RECIPES, {CONTROL: ("faulty", fix, scope)}):
            return remediate.remediate(CONTROL, self.repo, **kw)

    def assertRejected(self, fix, words, scope=SCOPE, **kw):
        report, code = self.run_recipe(fix, scope, **kw)
        self.assertEqual((code, report["status"]), (4, "rejected"), report)
        self.assertTrue(any(words in p for p in report["validation"]["problems"]), report["validation"])
        self.assertEqual(remediation_check._read_all(self.repo), self.before, "files were not rolled back")

    def append(self, rel, text):
        def fix(repo, dry):
            with (repo / rel).open("a", encoding="utf-8") as f:
                f.write(text)
            return [f"{rel}: faulty change"]
        return fix

    def test_good_recipe_passes_the_same_validator(self):
        report, code = self.run_recipe(self.append("terraform/s3.tf", GOOD))
        self.assertEqual((code, report["status"]), (0, "proven"), report)

    def test_modifying_an_existing_resource_is_rejected(self):
        def fix(repo, dry):
            f = repo / "terraform/s3.tf"
            f.write_text("# edited\n" + f.read_text(encoding="utf-8") + GOOD, encoding="utf-8")
            return ["s3.tf: edit"]
        self.assertRejected(fix, "modified, not only appended")

    def test_touching_an_undeclared_file_is_rejected(self):
        def fix(repo, dry):
            (repo / "configs/auth.yaml").write_text("mfa: {enforced_for_all_users: true}\n", encoding="utf-8")
            with (repo / "terraform/s3.tf").open("a", encoding="utf-8") as f:
                f.write(GOOD)
            return ["two files"]
        self.assertRejected(fix, "does not declare it")

    def test_deleting_a_file_is_rejected(self):
        def fix(repo, dry):
            (repo / "terraform/waf.tf").unlink() if (repo / "terraform/waf.tf").exists() else (repo / "terraform/iam.tf").unlink()
            return ["delete"]
        self.assertRejected(fix, "was deleted")

    def test_dangling_reference_is_rejected(self):
        bad = GOOD.replace("aws_kms_key.data.arn", "aws_kms_key.nope.arn")
        self.assertRejected(self.append("terraform/s3.tf", bad), "aws_kms_key.nope, which does not exist")

    def test_duplicate_resource_is_rejected(self):
        self.assertRejected(self.append("terraform/s3.tf", GOOD + GOOD), "duplicate resource")

    def test_undeclared_resource_type_is_rejected(self):
        extra = 'resource "aws_iam_user" "backdoor" {\n  name = "backdoor"\n}\n'
        self.assertRejected(self.append("terraform/s3.tf", GOOD + extra), "may not add")

    def test_broken_terraform_is_rejected(self):
        self.assertRejected(self.append("terraform/s3.tf", GOOD + 'resource "aws_s3_bucket" {{{\n'), "does not parse")

    def test_regression_of_another_control_is_rejected(self):
        """A fix that makes its own control pass while weakening a different, already-proven one."""
        block = GOOD + 'resource "aws_s3_bucket_public_access_block" "exports" {\n  bucket = aws_s3_bucket.exports.id\n  block_public_acls = false\n}\n'
        scope = remediation_check.Scope(SCOPE.files, SCOPE.new_types | {"aws_s3_bucket_public_access_block"})
        with mock.patch.object(remediation_check, "_regressions", wraps=remediation_check._regressions) as spy:
            self.assertRejected(self.append("terraform/s3.tf", block), "control", scope=scope)
            spy.assert_called()

    def test_yaml_recipe_cannot_change_other_keys(self):
        scope = remediation_check.Scope({"configs/auth.yaml": {"mfa.enforced_for_all_users"}})

        def fix(repo, dry):
            f = repo / "configs/auth.yaml"
            f.write_text(f.read_text(encoding="utf-8").replace("enforced_for_all_users: false", "enforced_for_all_users: true")
                         + "extra_admin: true\n", encoding="utf-8")
            return ["auth.yaml"]
        with mock.patch.dict(remediate.RECIPES, {"mfa_all_users": ("faulty", fix, scope)}):
            report, code = remediate.remediate("mfa_all_users", self.repo)
        self.assertEqual((code, report["status"]), (4, "rejected"), report)
        self.assertEqual(remediation_check._read_all(self.repo), self.before)

    def test_missing_tools_fail_closed_in_strict_mode(self):
        with mock.patch.object(remediation_check.shutil, "which", return_value=None):
            self.assertRejected(self.append("terraform/s3.tf", GOOD), "not installed", require_tools=True)

    def test_missing_tools_are_reported_not_hidden_otherwise(self):
        with mock.patch.object(remediation_check.shutil, "which", return_value=None):
            report, code = self.run_recipe(self.append("terraform/s3.tf", GOOD))
        self.assertEqual(code, 0)
        self.assertEqual(report["validation"]["skipped_tools"], ["terraform", "checkov"])

    def test_new_checkov_failure_is_rejected(self):
        calls = {"n": 0}

        def fake(argv, cwd):
            if argv[0] == "checkov":
                calls["n"] += 1
                failed = [] if calls["n"] == 1 else [{"check_id": "CKV_AWS_999", "resource": "aws_s3_bucket_server_side_encryption_configuration.exports"}]
                return 0, json.dumps({"results": {"failed_checks": failed}})
            return 0, ""

        with mock.patch.object(remediation_check.shutil, "which", side_effect=lambda t: "/bin/" + t), \
                mock.patch.object(remediation_check, "run_tool", fake):
            self.assertRejected(self.append("terraform/s3.tf", GOOD), "new failed check CKV_AWS_999")

    def test_terraform_fmt_failure_is_rejected(self):
        def fake(argv, cwd):
            return (3, "diff") if argv[:2] == ["terraform", "fmt"] else (0, "")

        with mock.patch.object(remediation_check.shutil, "which", side_effect=lambda t: "/bin/terraform" if t == "terraform" else None), \
                mock.patch.object(remediation_check, "run_tool", fake):
            self.assertRejected(self.append("terraform/s3.tf", GOOD), "terraform fmt")

    def test_unreadable_checkov_output_fails_closed(self):
        with mock.patch.object(remediation_check.shutil, "which", side_effect=lambda t: "/bin/checkov" if t == "checkov" else None), \
                mock.patch.object(remediation_check, "run_tool", lambda argv, cwd: (0, "not json")):
            with self.assertRaises(RuntimeError):
                remediation_check.baseline(self.repo)


if __name__ == "__main__":
    unittest.main()
