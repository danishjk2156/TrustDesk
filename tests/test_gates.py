"""Release gate tests: the packager may only ship what tools/check_gates.py passes.

    python -m unittest discover -s tests -v
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_gates  # noqa: E402

RUN = json.loads((ROOT / "tests" / "fixtures" / "run-40q-answers.json").read_text(encoding="utf-8"))
IDS = [a["id"] for a in RUN]  # the 40-question run recorded on Nov 5 (tests/fixtures)


def record(answers, qid):
    return next(a for a in answers if a["id"] == qid)


class Gates(unittest.TestCase):
    def setUp(self):
        self.answers = copy.deepcopy(RUN)

    def assertBlocked(self, qid, words):
        report = check_gates.check(self.answers, IDS)
        self.assertFalse(report["ok"])
        self.assertTrue(any(p["id"] == qid and words in p["problem"] for p in report["problems"]), report["problems"])

    def test_real_run_passes(self):
        self.assertEqual(check_gates.check(self.answers, IDS)["problems"], [])

    def test_missing_approval_blocks(self):
        record(self.answers, "Q1")["approved_by"] = None
        self.assertBlocked("Q1", "must be approved by security-lead")

    def test_security_lead_cannot_approve_a_commitment(self):
        record(self.answers, "Q39")["approved_by"] = "security-lead"
        self.assertBlocked("Q39", "must be approved by legal")

    def test_ae_cannot_approve_anything(self):
        record(self.answers, "Q2")["approved_by"] = "ae"
        self.assertBlocked("Q2", "must be approved by security-lead")

    def test_verified_without_citation_blocks(self):
        record(self.answers, "Q4")["evidence"] = []
        self.assertBlocked("Q4", "VERIFIED without a citation")

    def test_invented_citation_blocks(self):
        record(self.answers, "Q5")["evidence"] = ["acmecloud/terraform/alb.tf:31  backup_tested = true"]
        self.assertBlocked("Q5", "cited text does not match")

    def test_citation_outside_repo_blocks(self):
        record(self.answers, "Q6")["evidence"] = ["../../Windows/win.ini:1  x"]
        self.assertBlocked("Q6", "outside the repo")

    def test_needs_evidence_cannot_say_yes(self):
        record(self.answers, "Q33")["answer"] = "Yes, restores are tested."
        self.assertBlocked("Q33", 'goes out as "Yes"')

    def test_injection_that_relabels_everything_is_caught(self):
        """What a successful prompt injection would produce: every answer VERIFIED, no approvals."""
        for a in self.answers:
            a["label"], a["approved_by"] = "VERIFIED", None
        report = check_gates.check(self.answers, IDS)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(len(report["problems"]), 40)

    def test_missing_and_unknown_questions_block(self):
        self.answers = [a for a in self.answers if a["id"] != "Q10"] + [dict(record(self.answers, "Q1"), id="Q99")]
        report = check_gates.check(self.answers, IDS)
        problems = {(p["id"], p["problem"]) for p in report["problems"]}
        self.assertIn(("Q10", "no answer record"), problems)
        self.assertIn(("Q99", "record for a question that is not in the questionnaire"), problems)

    def test_cli_exit_codes(self):
        script = str(ROOT / "tools" / "check_gates.py")
        ok = subprocess.run([sys.executable, script, str(ROOT / "tests/fixtures/run-40q-answers.json"),
                             str(ROOT / "tests/fixtures/run-40q-completed.xlsx")], capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        blocked = subprocess.run([sys.executable, script, str(ROOT / "tests/fixtures/run-40q-answers.json"),
                                  str(ROOT / "samples/acme_questionnaire.xlsx")], capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 1)  # the 41-question sheet has a Q41 the old run never answered
        self.assertIn("Q41", blocked.stdout)

    def test_published_example_run_passes_on_the_41_question_sheet(self):
        """docs/example-run is what judges open; it must clear the real gate."""
        script = str(ROOT / "tools" / "check_gates.py")
        run = subprocess.run([sys.executable, script, str(ROOT / "docs/example-run/answers.json"),
                              str(ROOT / "samples/acme_questionnaire.xlsx")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout)
        answers = json.loads((ROOT / "docs/example-run/answers.json").read_text(encoding="utf-8"))
        verified = [a for a in answers if a["label"] == "VERIFIED"]
        self.assertTrue(verified and all(a.get("permalinks") for a in verified))


if __name__ == "__main__":
    unittest.main()
