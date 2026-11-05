"""TrustDesk release gate: decide in code, not by model judgment, whether a deal may be packaged.

The packager must run this and package only on exit code 0.

    python tools/check_gates.py work/answers.json samples/acme_questionnaire.xlsx

Checks every answer record against the questionnaire and the repo:
  1. every question has exactly one record, and no record is for an unknown question
  2. label is VERIFIED, NEEDS-EVIDENCE or COMMITMENT
  3. VERIFIED has citations, and every citation's `path:line  text` matches that line in the repo
     (a citation the evidence engine never produced cannot pass)
  4. COMMITMENT is approved by legal; every other label by security-lead; approved_at is ISO 8601
  5. NEEDS-EVIDENCE never goes out as "Yes"

Prints a JSON report; exit 0 = all gates pass, 1 = blocked, 2 = bad input.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def check(answers: list[dict], question_ids: list[str], root: Path = ROOT) -> dict:
    problems: list[dict] = []

    def bad(qid, msg):
        problems.append({"id": qid, "problem": msg})

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

    for a in answers:
        qid, label = str(a.get("id")), a.get("label")
        if label not in LABELS:
            bad(qid, f"unknown label {label!r}")
            continue
        if not (a.get("answer") or "").strip():
            bad(qid, "empty answer")
        if label == "VERIFIED":
            evidence = a.get("evidence") or []
            if not evidence:
                bad(qid, "VERIFIED without a citation")
            for cite in evidence:
                msg = _citation_problem(cite, root)
                if msg:
                    bad(qid, msg)
        if label == "NEEDS-EVIDENCE" and (a.get("answer") or "").strip().lower().startswith("yes"):
            bad(qid, 'NEEDS-EVIDENCE answer goes out as "Yes"')
        approver = "legal" if label == "COMMITMENT" else "security-lead"
        if a.get("approved_by") != approver:
            bad(qid, f"{label} must be approved by {approver}, not {a.get('approved_by')!r}")
        try:
            datetime.fromisoformat(str(a.get("approved_at")))
        except ValueError:
            bad(qid, f"approved_at is not an ISO 8601 time: {a.get('approved_at')!r}")

    return {"ok": not problems, "checked": len(answers), "questions": len(question_ids), "problems": problems}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT / "tools"))
    from xlsx_io import read_questions

    answers = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    ids = [q["id"] for q in read_questions(argv[1])]
    report = check(answers, ids)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
