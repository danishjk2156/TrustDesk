"""Release gate tests: the packager may only ship what tools/check_gates.py passes.

Every run here is drafted by the real evidence engine and approved with real Ed25519 signatures.

    python -m unittest discover -s tests -p "test_gates.py" -v
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support
from support import ROOT, gate, problems_for

sys.path.insert(0, str(ROOT / "tools"))
import approvals  # noqa: E402
from xlsx_io import read_questions  # noqa: E402

LEGACY = json.loads((ROOT / "tests" / "fixtures" / "run-40q-answers.json").read_text(encoding="utf-8"))


def record(answers, qid):
    return next(a for a in answers if a["id"] == qid)


class Gates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _ = support.draft(read_questions(support.SHEET))
        cls.entries = support.sign_run(cls.records)

    def setUp(self):
        self.answers = copy.deepcopy(self.records)

    def assertBlocked(self, qid, words, entries=None):
        report = gate(self.answers, self.entries if entries is None else entries)
        self.assertFalse(report["ok"])
        self.assertTrue(any(words in p for p in problems_for(report, qid)), report["problems"])

    def test_signed_run_passes(self):
        report = gate(self.answers, self.entries)
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["approvals"]["Q1"]["approver"], "sam")
        self.assertEqual(report["approvals"]["Q39"]["approver"], "lee")

    def test_no_ledger_blocks_every_answer(self):
        report = gate(self.answers, [])
        self.assertEqual({p["id"] for p in report["problems"]}, {a["id"] for a in self.answers})

    def test_self_asserted_approval_is_ignored(self):
        for a in self.answers:
            a["approved_by"] = "legal" if a["label"] == "COMMITMENT" else "security-lead"
            a["approved_at"] = "2026-11-06T10:00:00+05:30"
        self.assertBlocked("Q1", "no authenticated approval from security-lead", entries=[])

    def test_security_lead_cannot_approve_a_commitment(self):
        entries = [e for e in self.entries if e["answer_id"] != "Q39"]
        digest = support.digests(self.answers)["Q39"]
        entries.append(approvals.make_entry(support.SAM_KEY, "approval", support.DEAL, "Q39", digest, "legal", "sam"))
        self.assertBlocked("Q39", "no authenticated approval from legal", entries=entries)

    def test_legal_cannot_approve_a_fact(self):
        entries = [e for e in self.entries if e["answer_id"] != "Q2"]
        digest = support.digests(self.answers)["Q2"]
        entries.append(approvals.make_entry(support.LEE_KEY, "approval", support.DEAL, "Q2", digest, "security-lead", "lee"))
        self.assertBlocked("Q2", "no authenticated approval", entries=entries)

    def test_verified_without_citation_blocks(self):
        record(self.answers, "Q4")["evidence"] = []
        self.assertBlocked("Q4", "has no citation")

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
            a["label"], a["approved_by"] = "VERIFIED", "security-lead"
        report = gate(self.answers, [])
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(len(report["problems"]), 41)

    def test_missing_and_unknown_questions_block(self):
        self.answers = [a for a in self.answers if a["id"] != "Q10"] + [dict(record(self.answers, "Q1"), id="Q99")]
        report = gate(self.answers, self.entries)
        problems = {(p["id"], p["problem"]) for p in report["problems"]}
        self.assertIn(("Q10", "no answer record"), problems)
        self.assertIn(("Q99", "record for a question that is not in the questionnaire"), problems)

    def test_stale_run_cannot_pass_for_the_new_sheet(self):
        """Yesterday's 40-answer run must not clear today's 41-question sheet."""
        report = gate(copy.deepcopy(LEGACY), [])
        self.assertIn({"id": "Q41", "problem": "no answer record"}, report["problems"])

    def test_cli_blocks_without_a_ledger_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp) / "answers.json"
            answers.write_text(json.dumps(self.answers), encoding="utf-8")
            run = subprocess.run([sys.executable, str(ROOT / "tools" / "check_gates.py"), str(answers), str(support.SHEET),
                                  "--deal", "acme"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("no authenticated approval", run.stdout)

    def test_cli_bad_deal_id_is_bad_input(self):
        run = subprocess.run([sys.executable, str(ROOT / "tools" / "check_gates.py"), str(ROOT / "docs/example-run/answers.json"),
                              str(support.SHEET), "--deal", "../x"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)

    def test_published_example_run_is_blocked_by_the_strict_gate(self):
        """Recorded with self-asserted approvals and the text-search engine: it must not pass today's gate."""
        run = subprocess.run([sys.executable, str(ROOT / "tools" / "check_gates.py"), str(ROOT / "docs/example-run/answers.json"),
                              str(support.SHEET), "--deal", "example-run"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("no authenticated approval", run.stdout)


if __name__ == "__main__":
    unittest.main()
