"""Release blockers: a real file:line is not enough. The claim must be provable, relevant and unembellished.

    python -m unittest discover -s tests -p "test_adv_claims.py" -v
"""
import copy
import unittest

import support
from support import ROOT, gate, problems_for
from xlsx_io import read_questions

import claims  # noqa: E402 (support puts tools/ on the path)


class ClaimBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _ = support.draft(read_questions(support.SHEET))
        cls.verified = next(a for a in cls.records if a["label"] == "VERIFIED" and a["control"] == "db_encryption_at_rest")

    def problems(self, **changes):
        answer = {**copy.deepcopy(self.verified), **changes}
        return claims.verify_answer(answer, support.search.DEFAULT_REPO.resolve())[0]

    def test_untouched_answer_has_no_problems(self):
        self.assertEqual(self.problems(), [])

    def test_real_citation_for_the_wrong_control(self):
        other = next(a for a in self.records if a["label"] == "VERIFIED" and a["control"] == "kms_key_rotation")
        out = self.problems(evidence=other["evidence"])
        self.assertTrue(any("not evidence for db_encryption_at_rest" in p for p in out), out)

    def test_extra_real_but_unrelated_citation(self):
        other = next(a for a in self.records if a["label"] == "VERIFIED" and a["control"] == "kms_key_rotation")
        out = self.problems(evidence=self.verified["evidence"] + other["evidence"][:1])
        self.assertTrue(any("not evidence for" in p for p in out), out)

    def test_control_irrelevant_to_the_question(self):
        out = self.problems(question="Do you hold a SOC 2 Type II report and pentest annually?")
        self.assertTrue(any("not relevant" in p for p in out), out)

    def test_invented_number_is_rejected(self):
        out = self.problems(answer="Yes. Keys rotate every 17 days and backups are kept 400 days.")
        self.assertTrue(any("answer states" in p for p in out), out)

    def test_number_in_a_policy_sentence_is_allowed(self):
        self.assertEqual(self.problems(answer="Yes. Per policy (security-policy.md): reviews happen every 90 days."), [])

    def test_number_words_are_normalised(self):
        out = self.problems(answer="Yes, covering seven regions.")
        self.assertTrue(any("answer states 7" in p for p in out), out)

    def test_unknown_control_cannot_be_proven(self):
        out = self.problems(control="totally_secure", evidence=["acmecloud/terraform/rds.tf:1  x"])
        self.assertTrue(any("unknown control" in p for p in out), out)

    def test_no_control_no_proof(self):
        out = self.problems(control=None)
        self.assertTrue(any("without a control" in p for p in out), out)

    def test_explicit_claims_are_each_checked(self):
        other = next(a for a in self.records if a["label"] == "VERIFIED" and a["control"] == "kms_key_rotation")
        ok = {"control": "db_encryption_at_rest", "evidence": self.verified["evidence"]}
        bad = {"control": "kms_key_rotation", "evidence": self.verified["evidence"]}  # right control, someone else's lines
        out = self.problems(claims=[ok, bad], evidence=self.verified["evidence"], question="Is the database encrypted and are keys rotated?")
        self.assertTrue(any("not evidence for kms_key_rotation" in p for p in out), out)
        good = {"control": "kms_key_rotation", "evidence": other["evidence"]}
        out = self.problems(claims=[ok, good], evidence=self.verified["evidence"] + other["evidence"],
                            question="Is the database encrypted and are keys rotated?")
        self.assertEqual(out, [])

    def test_contradicted_control_cannot_be_verified(self):
        record = next(a for a in self.records if a["control"] == "storage_encryption_all_buckets")
        forged = {**copy.deepcopy(record), "label": "VERIFIED", "answer": "Yes.", "evidence": ["acmecloud/terraform/s3.tf:1  x"]}
        report = gate([forged] + [a for a in self.records if a["id"] != forged["id"]], [])
        self.assertTrue(any("contradicted in the code now" in p for p in problems_for(report, forged["id"])), report["problems"])

    def test_stale_evidence_after_a_code_change(self):
        """The line moved: the old citation no longer matches, even though the control still holds."""
        import shutil
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            copy_root = Path(tmp) / "acmecloud"
            shutil.copytree(support.search.DEFAULT_REPO, copy_root)
            tf = next(copy_root.glob("terraform/rds.tf"))
            tf.write_text("# new header\n# another\n" + tf.read_text(encoding="utf-8"), encoding="utf-8")
            out = claims.verify_answer(copy.deepcopy(self.verified), copy_root)[0]
        self.assertTrue(any("not evidence for db_encryption_at_rest" in p for p in out), out)

    def test_every_verified_answer_in_the_sample_run_binds(self):
        for a in self.records:
            if a["label"] == "VERIFIED":
                with self.subTest(a["id"]):
                    self.assertEqual(claims.verify_answer(a, support.search.DEFAULT_REPO.resolve())[0], [])


if __name__ == "__main__":
    unittest.main()
