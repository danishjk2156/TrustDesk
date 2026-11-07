"""Release blockers: the evidence engine must never report `found` for a setting the code does not make.

Each test builds a tiny product repo that LOOKS like it satisfies a control and asserts the engine
refuses. A failure here is a false-positive VERIFIED answer, which blocks release.

These three began as `expectedFailure` against the regex engine and now guard the structured verifier.
More cases: test_adv_structured.py.

    python -m unittest discover -s tests -p "test_adv_*.py" -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402


def repo_with(files: dict[str, str]) -> Path:
    """A throwaway product repo; keys are real relative paths such as `terraform/db.tf`."""
    top = Path(tempfile.mkdtemp(prefix="trustdesk-adv-"))
    repo = top / "product"
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


class FalsePositives(unittest.TestCase):
    def assertNotFound(self, control: str, repo: Path):
        result = search.check(control, repo)
        self.assertNotEqual(result["status"], "found", f"false positive for {control}: {result}")

    def test_commented_out_setting_is_not_proof(self):
        repo = repo_with({"terraform/db.tf": 'resource "aws_db_instance" "main" {\n  # storage_encrypted = true\n}\n'})
        self.assertNotFound("db_encryption_at_rest", repo)

    def test_one_encrypted_database_does_not_cover_an_unencrypted_one(self):
        repo = repo_with({"terraform/db.tf": (
            'resource "aws_db_instance" "main" {\n  storage_encrypted = true\n}\n'
            'resource "aws_db_instance" "reports" {\n  storage_encrypted = false\n}\n')})
        result = search.check("db_encryption_at_rest", repo)
        self.assertEqual(result["status"], "contradicted", result)

    def test_setting_on_the_wrong_resource_type_is_not_proof(self):
        repo = repo_with({"terraform/other.tf": 'resource "aws_instance" "batch" {\n  multi_az = true\n}\n'})
        self.assertNotFound("multi_az", repo)

    def test_unresolved_variable_is_not_proof(self):
        repo = repo_with({"terraform/db.tf": 'resource "aws_db_instance" "main" {\n  storage_encrypted = var.encrypt\n}\n'})
        self.assertNotFound("db_encryption_at_rest", repo)

    def test_string_true_is_not_boolean_true(self):
        repo = repo_with({"terraform/db.tf": 'resource "aws_db_instance" "main" {\n  storage_encrypted = "true-ish"\n}\n'})
        self.assertNotFound("db_encryption_at_rest", repo)

    def test_mfa_disabled_for_all_users_is_contradicted(self):
        repo = repo_with({"configs/auth.yaml": "mfa:\n  enforced_for_all_users: false\n"})
        self.assertEqual(search.check("mfa_all_users", repo)["status"], "contradicted")


if __name__ == "__main__":
    unittest.main()
