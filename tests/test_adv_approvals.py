"""Release blockers: spoofed, stale, replayed or mis-scoped approvals must never release an answer.

    python -m unittest discover -s tests -p "test_adv_approvals.py" -v
"""
import copy
import unittest
from datetime import datetime, timedelta, timezone

import support
from support import ROOT, gate, problems_for
from xlsx_io import read_questions

import approvals  # noqa: E402 (support puts tools/ on the path)

NOW = datetime.now(timezone.utc)


class SpoofedApprovals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, _ = support.draft(read_questions(support.SHEET))
        cls.entries = support.sign_run(cls.records)

    def setUp(self):
        self.answers = copy.deepcopy(self.records)

    def only(self, qid, entries):
        """The whole run is signed except `qid`, which gets exactly `entries`."""
        return [e for e in self.entries if e["answer_id"] != qid] + entries

    def entry(self, qid, *, key=support.SAM_KEY, kind="approval", role="security-lead", who="sam", deal=support.DEAL, at=None,
              digest=None):
        d = digest or support.digests(self.answers)[qid]
        return approvals.make_entry(key, kind, deal, qid, d, role, who, at)

    def blocked(self, qid, entries, words):
        report = gate(self.answers, self.only(qid, entries))
        self.assertFalse(report["ok"])
        self.assertTrue(any(words in p for p in problems_for(report, qid)), report["problems"])

    def test_good_entry_passes(self):
        self.assertTrue(gate(self.answers, self.only("Q1", [self.entry("Q1")]))["ok"])

    def test_signed_by_a_key_outside_the_roster(self):
        self.blocked("Q1", [self.entry("Q1", key=support.EVE_KEY, who="eve")], "no authenticated approval")

    def test_impersonating_a_roster_name_with_the_wrong_key(self):
        self.blocked("Q1", [self.entry("Q1", key=support.EVE_KEY, who="sam")], "does not verify")

    def test_roster_person_in_the_wrong_role(self):
        self.blocked("Q1", [self.entry("Q1", key=support.LEE_KEY, who="lee", role="legal")], "no authenticated approval")
        self.blocked("Q1", [self.entry("Q1", key=support.LEE_KEY, who="lee", role="security-lead")], "not in the roster")

    def test_tampered_entry_fails_signature(self):
        e = self.entry("Q1")
        e["role"] = "legal"
        self.blocked("Q1", [e], "no authenticated approval")
        e = self.entry("Q2")
        e["digest"] = "0" * 64
        self.blocked("Q2", [e], "does not verify")

    def test_replay_after_the_answer_text_changes(self):
        old = self.entry("Q3")
        next(a for a in self.answers if a["id"] == "Q3")["answer"] = "Yes, and we also guarantee 100% uptime."
        self.blocked("Q3", [old], "different version")

    def test_replay_after_the_evidence_changes(self):
        old = self.entry("Q4")
        rec = next(a for a in self.answers if a["id"] == "Q4")
        rec["evidence"] = rec["evidence"][:-1] or rec["evidence"]
        rec["control"] = "waf" if rec["control"] != "waf" else "hsts"
        report = gate(self.answers, self.only("Q4", [old]))
        self.assertFalse(report["ok"])
        self.assertTrue(problems_for(report, "Q4"))

    def test_replay_to_another_question(self):
        e = self.entry("Q5")
        e["answer_id"] = "Q6"
        self.blocked("Q6", [e], "does not verify")

    def test_replay_to_another_deal(self):
        self.blocked("Q1", [self.entry("Q1", deal="other-deal")], "other-deal")

    def test_expired_approval(self):
        self.blocked("Q1", [self.entry("Q1", at=NOW - timedelta(days=31))], "expired")

    def test_future_dated_approval(self):
        self.blocked("Q1", [self.entry("Q1", at=NOW + timedelta(days=2))], "future")

    def test_revoked_approval(self):
        at = NOW - timedelta(hours=2)
        later = NOW - timedelta(hours=1)
        entries = [self.entry("Q1", at=at), self.entry("Q1", kind="revoke", at=later)]
        self.blocked("Q1", entries, "no authenticated approval")

    def test_reapproval_after_revoke_is_accepted(self):
        entries = [self.entry("Q1", at=NOW - timedelta(hours=3)), self.entry("Q1", kind="revoke", at=NOW - timedelta(hours=2)),
                   self.entry("Q1", at=NOW - timedelta(hours=1))]
        # a later re-approval is a new entry; the revoke only cancels approvals made before it
        report = gate(self.answers, self.only("Q1", entries))
        self.assertTrue(report["ok"], report["problems"])

    def test_roster_pin_detects_an_edited_roster(self):
        report = gate(self.answers, self.entries, roster_sha256="a" * 64, expect_roster_sha256="b" * 64)
        self.assertIn({"id": "roster", "problem": "approver roster does not match the pinned hash; it may have been edited"},
                      report["problems"])

    def test_naive_timestamp_is_rejected(self):
        e = self.entry("Q1")
        e["at"] = "2026-11-06T10:00:00"  # no timezone
        self.blocked("Q1", [e], "no authenticated approval")

    def test_github_provider_needs_online_verification(self):
        e = {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": support.digests(self.answers)["Q1"],
             "role": "security-lead", "approver": "sam", "at": NOW.isoformat(), "provider": "github",
             "review": {"repo": "o/r", "pr": 1, "review_id": 2, "commit": "a" * 40}}
        self.blocked("Q1", [e], "--verify-github")

    def test_github_review_by_someone_else_is_rejected(self):
        digest = support.digests(self.answers)["Q1"]

        class Fake:
            def review(self, ref):
                return {"login": "mallory", "state": "APPROVED", "commit_id": ref["commit"], "submitted_at": NOW.isoformat(), "merged": True, "base": "main", "head_sha": ref["commit"], "on_main": True}

            def digests(self, ref, deal):
                return {"Q1": digest}

        e = {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": digest, "role": "security-lead",
             "approver": "sam", "at": NOW.isoformat(), "provider": "github",
             "review": {"repo": "o/r", "pr": 1, "review_id": 2, "commit": "a" * 40}}
        report = gate(self.answers, self.only("Q1", [e]), github=Fake(), github_repo="o/r")
        self.assertFalse(report["ok"])

    def test_github_review_by_the_right_person_is_accepted(self):
        digest = support.digests(self.answers)["Q1"]

        class Fake:
            def review(self, ref):
                return {"login": "sam-gh", "state": "APPROVED", "commit_id": ref["commit"], "submitted_at": NOW.isoformat(), "merged": True, "base": "main", "head_sha": ref["commit"], "on_main": True}

            def digests(self, ref, deal):
                return {"Q1": digest}

        e = {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": digest, "role": "security-lead",
             "approver": "sam", "at": NOW.isoformat(), "provider": "github",
             "review": {"repo": "o/r", "pr": 1, "review_id": 2, "commit": "a" * 40}}
        self.assertTrue(gate(self.answers, self.only("Q1", [e]), github=Fake(), github_repo="o/r")["ok"])

    def test_github_review_for_a_different_digest_manifest_is_rejected(self):
        digest = support.digests(self.answers)["Q1"]

        class Fake:
            def review(self, ref):
                return {"login": "sam-gh", "state": "APPROVED", "commit_id": ref["commit"], "submitted_at": NOW.isoformat(), "merged": True, "base": "main", "head_sha": ref["commit"], "on_main": True}

            def digests(self, ref, deal):
                return {"Q1": "f" * 64}

        e = {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": digest, "role": "security-lead",
             "approver": "sam", "at": NOW.isoformat(), "provider": "github",
             "review": {"repo": "o/r", "pr": 1, "review_id": 2, "commit": "a" * 40}}
        self.blocked_with(e)

    def blocked_with(self, e):
        class Fake:
            def review(self, ref):
                return {"login": "sam-gh", "state": "APPROVED", "commit_id": ref["commit"], "submitted_at": NOW.isoformat(), "merged": True, "base": "main", "head_sha": ref["commit"], "on_main": True}

            def digests(self, ref, deal):
                return {"Q1": "f" * 64}

        self.assertFalse(gate(self.answers, self.only("Q1", [e]), github=Fake(), github_repo="o/r")["ok"])

    def _github_entry(self, repo="o/r"):
        return {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": support.digests(self.answers)["Q1"],
                "role": "security-lead", "approver": "sam", "at": NOW.isoformat(), "provider": "github",
                "review": {"repo": repo, "pr": 1, "review_id": 2, "commit": "a" * 40}}

    def _fake(self, **review):
        digest = support.digests(self.answers)["Q1"]

        class Fake:
            def review(self, ref):
                return {"login": "sam-gh", "state": "APPROVED", "commit_id": ref["commit"], "submitted_at": NOW.isoformat(),
                        "merged": True, "base": "main", "head_sha": ref["commit"], "on_main": True, **review}

            def digests(self, ref, deal):
                return {"Q1": digest}

        return Fake()

    def test_github_review_from_an_unpinned_repo_is_rejected(self):
        e = self._github_entry(repo="attacker/fork")
        report = gate(self.answers, self.only("Q1", [e]), github=self._fake(), github_repo="o/r")
        self.assertTrue(any("pins" in p for p in problems_for(report, "Q1")), report["problems"])

    def test_github_approval_without_a_pinned_repo_is_rejected(self):
        report = gate(self.answers, self.only("Q1", [self._github_entry()]), github=self._fake())
        self.assertFalse(report["ok"])

    def test_github_review_on_an_unmerged_pr_is_rejected(self):
        report = gate(self.answers, self.only("Q1", [self._github_entry()]), github=self._fake(merged=False), github_repo="o/r")
        self.assertTrue(any("not merged" in p for p in problems_for(report, "Q1")), report["problems"])

    def test_github_review_of_a_commit_that_is_not_what_was_merged_is_rejected(self):
        for change in ({"head_sha": "b" * 40}, {"on_main": False}):
            with self.subTest(change):
                report = gate(self.answers, self.only("Q1", [self._github_entry()]), github=self._fake(**change), github_repo="o/r")
                self.assertTrue(any("not what was merged" in p for p in problems_for(report, "Q1")), report["problems"])

    def test_github_review_merged_into_another_branch_is_rejected(self):
        report = gate(self.answers, self.only("Q1", [self._github_entry()]), github=self._fake(base="feature"), github_repo="o/r")
        self.assertFalse(report["ok"])

    def test_dot_only_repo_segments_are_malformed(self):
        for repo in ("../x", "o/..", "./r", "o/r/extra"):
            out, why = approvals._check_entry(self._github_entry(repo=repo), support.ROSTER, support.DEAL, object(), "o/r")
            self.assertIsNone(out, repo)

    def test_unpinned_roster_fails_closed_in_evaluate(self):
        import os
        import tempfile
        from pathlib import Path
        import check_gates
        os.environ.pop(check_gates.PIN_ENV, None)
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp) / "answers.json"
            answers.write_text("[]", encoding="utf-8")
            report, _ = check_gates.evaluate(answers, support.SHEET, "acme")
        self.assertFalse(report["ok"])
        self.assertTrue(any(p["id"] == "roster" and "pinned" in p["problem"] for p in report["problems"]))

    def test_malformed_github_reference_is_rejected_before_any_call(self):
        e = {"type": "approval", "deal": support.DEAL, "answer_id": "Q1", "digest": "d", "role": "security-lead",
             "approver": "sam", "at": NOW.isoformat(), "provider": "github",
             "review": {"repo": "o/r; rm -rf /", "pr": 1, "review_id": 2, "commit": "a" * 40}}
        out, why = approvals._check_entry(e, support.ROSTER, support.DEAL, object())
        self.assertIsNone(out)
        self.assertIn("malformed", why)

    def test_commitment_is_invalidated_when_a_policy_document_changes(self):
        base = next(a for a in self.answers if a["label"] == "COMMITMENT")
        before = approvals.digest_of(base, {"claims": []})
        original = approvals.policy_hashes
        approvals.policy_hashes = lambda: {"room-docs/pricing.md": "changed"}
        try:
            self.assertNotEqual(before, approvals.digest_of(base, {"claims": []}))
        finally:
            approvals.policy_hashes = original


if __name__ == "__main__":
    unittest.main()
