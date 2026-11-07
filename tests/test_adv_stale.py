"""Release blockers: an approval must not survive a change to the code, the evidence or the policy behind it.

    python -m unittest discover -s tests -p "test_adv_stale.py" -v
"""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support
from support import ROOT, gate, problems_for
from xlsx_io import read_questions

import approvals  # noqa: E402 (support puts tools/ on the path)


class StaleEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _ = support.draft(read_questions(support.SHEET))
        cls.entries = support.sign_run(cls.records)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "acmecloud"
        shutil.copytree(ROOT / "acmecloud", self.repo)
        self.answers = copy.deepcopy(self.records)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, rel, old, new):
        f = self.repo / rel
        text = f.read_text(encoding="utf-8")
        self.assertIn(old, text)
        f.write_text(text.replace(old, new, 1), encoding="utf-8")

    def gate_here(self):
        return gate(self.answers, self.entries, repo=self.repo, root=self.tmp)

    def test_approved_run_still_passes_on_an_untouched_copy(self):
        entries = support.sign_run(self.answers, repo=self.repo)
        self.assertTrue(gate(self.answers, entries, repo=self.repo, root=self.tmp)["ok"])

    def test_control_turned_off_after_approval_blocks(self):
        entries = support.sign_run(self.answers, repo=self.repo)
        self.edit("terraform/rds.tf", "storage_encrypted = true", "storage_encrypted = false")
        report = gate(self.answers, entries, repo=self.repo, root=self.tmp)
        qids = [a["id"] for a in self.answers if a["control"] == "db_encryption_at_rest"]
        self.assertTrue(qids)
        for qid in qids:
            self.assertTrue(any("in the code now" in p or "not evidence" in p or "does not match" in p
                                for p in problems_for(report, qid)), (qid, report["problems"]))

    def test_line_shift_after_approval_blocks(self):
        entries = support.sign_run(self.answers, repo=self.repo)
        rds = self.repo / "terraform/rds.tf"
        rds.write_text("# header added later\n" + rds.read_text(encoding="utf-8"), encoding="utf-8")
        report = gate(self.answers, entries, repo=self.repo, root=self.tmp)
        self.assertTrue(any(problems_for(report, a["id"]) for a in self.answers if a["control"] == "db_encryption_at_rest"))

    def test_harmless_edit_elsewhere_changes_the_digest_of_nothing_unrelated(self):
        """Approvals are bound to the files the proof rests on, so unrelated edits do not churn them."""
        entries = support.sign_run(self.answers, repo=self.repo)
        (self.repo / "NOTES.md").write_text("scratch\n", encoding="utf-8")
        self.assertTrue(gate(self.answers, entries, repo=self.repo, root=self.tmp)["ok"])

    def test_same_value_rewritten_in_a_proof_file_requires_reapproval(self):
        """Even a no-op reformat of a proved file changes its hash, so the reviewer sees the change again."""
        entries = support.sign_run(self.answers, repo=self.repo)
        self.edit("terraform/kms.tf", "enable_key_rotation     = true", "enable_key_rotation     = true  # reviewed")
        report = gate(self.answers, entries, repo=self.repo, root=self.tmp)
        rotated = [a["id"] for a in self.answers if a["control"] == "kms_key_rotation"]
        self.assertTrue(any("different version" in p or "does not match" in p or "not evidence" in p
                            for qid in rotated for p in problems_for(report, qid)), report["problems"])

    def test_policy_edit_invalidates_a_commitment_approval(self):
        commitment = next(a["id"] for a in self.answers if a["label"] == "COMMITMENT")
        with mock.patch.object(approvals, "policy_hashes", lambda: {"room-docs/encryption-policy.md": "edited"}):
            report = gate(self.answers, self.entries)
        self.assertTrue(any("different version" in p for p in problems_for(report, commitment)), report["problems"])

    def test_policy_edit_does_not_touch_factual_approvals(self):
        with mock.patch.object(approvals, "policy_hashes", lambda: {"room-docs/encryption-policy.md": "edited"}):
            report = gate(self.answers, self.entries)
        factual = [a["id"] for a in self.answers if a["label"] != "COMMITMENT"]
        self.assertFalse(any(problems_for(report, qid) for qid in factual))

    def test_old_question_text_cannot_be_reused_for_a_changed_question(self):
        rec = next(a for a in self.answers if a["id"] == "Q1")
        rec["question"] = "Is the database encrypted with a customer-managed key under our own HSM?"
        report = gate(self.answers, self.entries)
        self.assertTrue(any("differs from the questionnaire" in p for p in problems_for(report, "Q1")))


if __name__ == "__main__":
    unittest.main()
