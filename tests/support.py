"""Shared test helpers: draft the 41-question sample the way the drafter does, then approve it with real
Ed25519 signatures from test approvers, so gate tests exercise the same path as a release.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import approvals  # noqa: E402
import check_gates  # noqa: E402
import search  # noqa: E402
from xlsx_io import read_questions  # noqa: E402

SHEET = ROOT / "samples" / "acme_questionnaire.xlsx"
KEY = {q["id"]: q for q in json.loads((ROOT / "tests" / "answer_key.json").read_text(encoding="utf-8"))}
LABEL = {"found": "VERIFIED", "missing": "NEEDS-EVIDENCE", "contradicted": "NEEDS-EVIDENCE", "unverifiable": "NEEDS-EVIDENCE"}
DEAL = "acme"

SAM_KEY, SAM_PUB = approvals.keygen()  # security lead
LEE_KEY, LEE_PUB = approvals.keygen()  # legal
EVE_KEY, EVE_PUB = approvals.keygen()  # not in the roster
ROSTER = {
    "sam": {"roles": {"security-lead"}, "github": "sam-gh", "public_key": SAM_PUB},
    "lee": {"roles": {"legal"}, "github": "lee-gh", "public_key": LEE_PUB},
}
SIGNERS = {"security-lead": ("sam", SAM_KEY), "legal": ("lee", LEE_KEY)}


def draft(questions, repo=None):
    """Drafter steps 2-3: label, tag, one batched claims message, apply the evidence reply."""
    records, claims = [], []
    for q in questions:
        control = KEY[q["id"]]["control"]
        rec = {"id": q["id"], "question": q["question"], "control": control, "evidence": []}
        if control is None:
            rec.update(label="COMMITMENT", answer="Per policy: see the committed figure.")
        else:
            claims.append({"id": q["id"], "control": control, "claim": q["question"]})
        records.append(rec)
    reply = search.run({"type": "claims", "claims": claims}, repo or search.DEFAULT_REPO)
    by_id = {r["id"]: r for r in reply["results"]}
    for rec in records:
        r = by_id.get(rec["id"])
        if not r:
            continue
        rec["label"] = LABEL[r["status"]]
        rec["evidence"] = r.get("evidence", [])
        rec["permalinks"] = r.get("permalinks")
        rec["answer"] = "Yes." if r["status"] == "found" else (
            "Partially / no: " + r["reason"] if r["status"] == "contradicted" else "Unknown: " + r["reason"])
    return records, reply


def digests(records, repo=None):
    repo = (repo or search.DEFAULT_REPO).resolve()
    return {a["id"]: approvals.digest_of(a, approvals.proof_of(a, repo)[1]) for a in records}


def sign_run(records, repo=None, deal=DEAL, at=None, only=None):
    """Ledger entries: security-lead for facts, legal for commitments."""
    entries = []
    for qid, digest in digests(records, repo).items():
        if only and qid not in only:
            continue
        rec = next(a for a in records if a["id"] == qid)
        role = approvals.required_role(rec["label"])
        who, key = SIGNERS[role]
        entries.append(approvals.make_entry(key, "approval", deal, qid, digest, role, who, at))
    return entries


def gate(records, entries, repo=None, root=ROOT, deal=DEAL, now=None, **kw):
    questions = read_questions(SHEET)
    return check_gates.check(records, [q["id"] for q in questions], root, question_texts={q["id"]: q["question"] for q in questions},
                             repo=repo, ledger=entries, roster=kw.pop("roster", ROSTER), deal=deal,
                             now=now or datetime.now(timezone.utc), **kw)


def problems_for(report, qid):
    return [p["problem"] for p in report["problems"] if p["id"] == qid]
