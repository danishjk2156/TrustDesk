"""Red team tests: promises that disagree with the room's own documents never go out silently.

    python -m unittest discover -s tests -p "test_red_team.py" -v
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import red_team  # noqa: E402

RUN = json.loads((ROOT / "docs" / "example-run" / "answers.json").read_text(encoding="utf-8"))


def fresh_run():
    """The published run as it looks before anyone approves anything."""
    answers = copy.deepcopy(RUN)
    for a in answers:
        a["approved_by"] = a["approved_at"] = None
    return answers


def find(report, kind, qid, topic):
    return [f for f in report[kind] if f["id"] == qid and f["topic"] == topic]


class RedTeam(unittest.TestCase):
    def test_marketing_faq_24h_conflicts_with_the_72h_promise(self):
        report = red_team.review(fresh_run())
        hits = find(report, "conflicts", "Q39", "breach notification")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["doc_says"], 24)
        self.assertEqual(hits[0]["answer_says"], [72])
        self.assertTrue(hits[0]["where"].startswith("room-docs/customer-faq.md:"))
        self.assertEqual(hits[0]["owner"], "legal")

    def test_uptime_conflicts_with_policy_and_faq_once_each(self):
        hits = find(red_team.review(fresh_run()), "conflicts", "Q40", "uptime")
        self.assertEqual(sorted(h["doc_says"] for h in hits), [99.9, 99.99])

    def test_legal_approval_turns_conflicts_into_accepted_differences(self):
        report = red_team.review(RUN)
        self.assertEqual(report["conflicts"], [])
        self.assertTrue(find(report, "accepted", "Q39", "breach notification"))

    def test_security_lead_cannot_accept_a_promise_conflict(self):
        answers = fresh_run()
        next(a for a in answers if a["id"] == "Q39")["approved_by"] = "security-lead"
        self.assertTrue(find(red_team.review(answers), "conflicts", "Q39", "breach notification"))

    def test_matching_figures_raise_nothing(self):
        """Q18 says 365 days and the incident-response policy says 365 days."""
        report = red_team.review(fresh_run())
        self.assertFalse([f for f in report["conflicts"] if f["id"] in ("Q18", "Q20")])

    def test_needs_evidence_answers_are_not_red_teamed(self):
        answers = fresh_run()
        q39 = next(a for a in answers if a["id"] == "Q39")
        q39["label"] = "NEEDS-EVIDENCE"
        self.assertFalse(find(red_team.review(answers), "conflicts", "Q39", "breach notification"))

    def test_cli_exit_codes(self):
        script = str(ROOT / "tools" / "red_team.py")
        ok = subprocess.run([sys.executable, script, str(ROOT / "docs/example-run/answers.json")], capture_output=True)
        self.assertEqual(ok.returncode, 0)
        tmp = ROOT / "tests" / "fixtures" / "_unapproved.json"
        try:
            tmp.write_text(json.dumps(fresh_run()), encoding="utf-8")
            bad = subprocess.run([sys.executable, script, str(tmp), "--markdown"], capture_output=True,
                                 text=True, encoding="utf-8")
            self.assertEqual(bad.returncode, 1)
            self.assertIn("customer-faq.md", bad.stdout)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
