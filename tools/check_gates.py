"""TrustDesk release gate: decide in code, not by model judgment, whether a deal may be packaged.

The packager must run this and package only on exit code 0.

    python tools/check_gates.py work/acme/answers.json samples/acme_questionnaire.xlsx --deal acme

Checks every answer record against the questionnaire, the code and the approval ledger:
  1. every question has exactly one record, no record is for an unknown question, and the record's
     question text is the questionnaire's
  2. label is VERIFIED, NEEDS-EVIDENCE or COMMITMENT; NEEDS-EVIDENCE never goes out as "Yes"
  3. VERIFIED is re-proven now (tools/claims.py): the control fits the question, the verifier says
     `found`, every citation is evidence for that control, and every number is supported
  4. every answer has an authenticated approval from the right role (security-lead for facts, legal
     for COMMITMENT) for the digest of THIS version of the answer and its evidence (tools/approvals.py)
  5. no red-team conflict is open (an answer disagreeing with a room document needs its owner's approval)

The roster is pinned: `evaluate` (CLI and MCP) refuses to pass unless TRUSTDESK_ROSTER_SHA256 or
--roster-sha256 matches the roster's hash. `approved_by` inside an answer record is never read. Prints a JSON report; exit 0 = all gates pass,
1 = blocked, 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import approvals  # noqa: E402
import claims  # noqa: E402
import red_team  # noqa: E402

PIN_ENV = "TRUSTDESK_ROSTER_SHA256"  # kept outside the repo (CI secret or the approver's shell)
LABELS = {"VERIFIED", "NEEDS-EVIDENCE", "COMMITMENT"}
CITE = re.compile(r"^(?P<path>[\w./-]+):(?P<line>\d+)  (?P<text>.+)$")


def _citation_problem(cite: str, root: Path) -> str | None:
    m = CITE.match(cite)
    if not m:
        return f"citation not in 'path:line  text' form: {cite!r}"
    path = (root / m["path"]).resolve()
    if root.resolve() not in path.parents:
        return f"cited file is outside the repo: {m['path']}"
    if not path.is_file():
        return f"cited file does not exist: {m['path']}"
    lines = path.read_text(encoding="utf-8").splitlines()
    n = int(m["line"])
    if not 1 <= n <= len(lines):
        return f"cited line {n} is past the end of {m['path']}"
    if lines[n - 1].strip() != m["text"].strip():
        return f"cited text does not match {m['path']}:{n}"
    return None


def _same_text(a, b) -> bool:
    return " ".join(str(a).split()) == " ".join(str(b).split())


def _approval_problem(qid: str, role: str, digest: str, everything: list[approvals.Approval], rejected: list[str]) -> str:
    mine = [a for a in everything if a.answer_id == qid]
    if any(a.role == role and a.digest != digest for a in mine):
        return f"approval is for a different version of this answer or its evidence; {role} must approve again"
    why = next((r for r in rejected if r.startswith(f"{qid}:")), None)
    return f"no authenticated approval from {role}" + (f" ({why.split(': ', 1)[1]})" if why else "")


def check(answers: list[dict], question_ids: list[str], root: Path = ROOT, *, question_texts: dict[str, str] | None = None,
          repo: Path | None = None, ledger: list[dict] | None = None, roster: dict | None = None, deal: str = "default",
          now: datetime | None = None, github: approvals.GitHubSource | None = None, roster_sha256: str | None = None,
          expect_roster_sha256: str | None = None, github_repo: str | None = None) -> dict:
    problems: list[dict] = []
    repo = (repo or claims.search.DEFAULT_REPO).resolve()
    now = now or datetime.now(timezone.utc)

    def bad(qid, msg):
        problems.append({"id": qid, "problem": msg})

    if expect_roster_sha256 and roster_sha256 != expect_roster_sha256:
        bad("roster", "approver roster does not match the pinned hash; it may have been edited")
    live, rejected = approvals.valid_approvals(ledger or [], roster or {}, deal, now, github, github_repo=github_repo)
    index: dict[tuple[str, str], list[approvals.Approval]] = {}
    for a in live:
        index.setdefault((a.answer_id, a.digest), []).append(a)

    seen: dict[str, int] = {}
    for a in answers:
        seen[str(a.get("id"))] = seen.get(str(a.get("id")), 0) + 1
    for qid in question_ids:
        if qid not in seen:
            bad(qid, "no answer record")
    for qid, count in seen.items():
        if qid not in question_ids:
            bad(qid, "record for a question that is not in the questionnaire")
        elif count > 1:
            bad(qid, f"{count} records for one question")

    digests: dict[str, str] = {}
    approved: dict[str, dict] = {}
    for a in answers:
        qid, label = str(a.get("id")), a.get("label")
        if label not in LABELS:
            bad(qid, f"unknown label {label!r}")
            continue
        if not (a.get("answer") or "").strip():
            bad(qid, "empty answer")
        if question_texts and qid in question_texts and not _same_text(a.get("question"), question_texts[qid]):
            bad(qid, "question text differs from the questionnaire")
            continue
        subject = {**a, "question": (question_texts or {}).get(qid, a.get("question"))}
        if label == "NEEDS-EVIDENCE" and (a.get("answer") or "").strip().lower().startswith("yes"):
            bad(qid, 'NEEDS-EVIDENCE answer goes out as "Yes"')
        if label == "VERIFIED":
            for cite in a.get("evidence") or []:
                if (msg := _citation_problem(cite, root)):
                    bad(qid, msg)
        found, proof = approvals.proof_of(subject, repo)
        for msg in found:
            bad(qid, msg)
        digests[qid] = approvals.digest_of(subject, proof)
        role = approvals.required_role(label)
        granted = [x for x in index.get((qid, digests[qid]), []) if x.role == role]
        if granted:
            approved[qid] = {"approver": granted[0].approver, "role": role, "at": granted[0].at.isoformat()}
        else:
            bad(qid, _approval_problem(qid, role, digests[qid], live, rejected))

    roles = {qid: {v["role"]} for qid, v in approved.items()}
    for conflict in red_team.review([{**a, "question": (question_texts or {}).get(str(a.get("id")), a.get("question"))}
                                     for a in answers], roles)["conflicts"]:
        bad(conflict["id"], f"open red-team conflict: {conflict['ask']}")

    return {"ok": not problems, "checked": len(answers), "questions": len(question_ids), "problems": problems,
            "digests": digests, "approvals": approved, "roster_sha256": roster_sha256,
            "rejected_approvals": rejected}


def evaluate(answers_path, questionnaire, deal: str | None = None, *, ledger: str | None = None, roster_path: str | None = None,
             roster_sha256: str | None = None, repo: Path | None = None, verify_github: bool = False) -> tuple[dict, list[dict]]:
    """Load everything from disk and run `check`. Shared by the CLI and the MCP server."""
    from xlsx_io import read_questions

    answers_path = Path(answers_path)
    deal = deal or answers_path.resolve().parent.name
    if not approvals.DEAL_ID.match(deal):
        raise ValueError(f"bad deal id {deal!r}: use lowercase letters, digits, - or _")
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    questions = read_questions(questionnaire)
    roster_file = Path(roster_path) if roster_path else approvals.ROSTER
    roster, roster_hash = approvals.load_roster(roster_file) if roster_file.is_file() else ({}, None)
    github_repo = approvals.load_github_repo(roster_file) if roster_file.is_file() else None
    pin = roster_sha256 or os.environ.get(PIN_ENV)
    entries, ledger_problems = approvals.read_ledger(Path(ledger) if ledger else answers_path.with_name("approvals.jsonl"))
    report = check(answers, [q["id"] for q in questions], question_texts={q["id"]: q["question"] for q in questions},
                   repo=repo, ledger=entries, roster=roster, deal=deal, roster_sha256=roster_hash,
                   expect_roster_sha256=pin, github=approvals.GitHubSource() if verify_github else None, github_repo=github_repo)
    report["rejected_approvals"] += ledger_problems
    if not pin:  # the roster is the trust anchor: unpinned, anyone who can edit the repo can add an approver
        report["problems"].append({"id": "roster", "problem": f"no pinned roster hash; set {PIN_ENV} or pass --roster-sha256"})
        report["ok"] = False
    return report, answers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("answers")
    ap.add_argument("questionnaire")
    ap.add_argument("--deal", help="deal id (default: the answers file's folder name)")
    ap.add_argument("--ledger", help="approvals.jsonl (default: next to the answers file)")
    ap.add_argument("--roster", default=str(approvals.ROSTER))
    ap.add_argument("--roster-sha256", help="pinned hash of the roster; the gate fails if the file differs")
    ap.add_argument("--repo", type=Path, default=claims.search.DEFAULT_REPO)
    ap.add_argument("--verify-github", action="store_true", help="confirm GitHub-review approvals with the gh CLI")
    args = ap.parse_args(argv)
    try:
        report, _ = evaluate(args.answers, args.questionnaire, args.deal, ledger=args.ledger, roster_path=args.roster,
                             roster_sha256=args.roster_sha256, repo=args.repo, verify_github=args.verify_github)
    except (OSError, ValueError) as exc:
        print(f"check_gates: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
