"""TrustDesk red team: find answers that disagree with what the room's own documents say.

Run after the evidence-checker and before the drafter posts its summary. Deterministic, like the
evidence engine: it compares the numbers each promise makes against every room document, so the
same answers always get the same findings.

    python tools/red_team.py work/answers.json
    python tools/red_team.py work/answers.json --markdown

Findings:
  conflict  an answer's figure differs from a figure in a room document on the same topic, and no
            one has approved the difference yet. The drafter must fix it or send it to the owner
            (Legal for promises); a conflict never goes out silently.
  accepted  the same kind of difference, already approved by the right person (e.g. Legal chose
            99.5% over the policy's 99.9%). Reported for the record, not blocking.

Exit 0 = no open conflicts, 1 = open conflicts, 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [*sorted((ROOT / "room-docs").glob("*.md")), ROOT / "acmecloud" / "SECURITY.md"]
SKIP_DOCS = {"controls.md"}

# Topics promises are made about: how to spot one in a question or a document line, and the unit
# whose figure must agree.
TOPICS = {
    "breach notification": {"text": r"breach|notif", "line": r"notif", "figure": r"(\d+(?:\.\d+)?)\s*hours?"},
    "uptime": {"text": r"uptime|availab", "line": r"uptime|availab", "figure": r"(\d+(?:\.\d+)?)\s*%"},
    "vulnerability response": {"text": r"vulnerab|report", "line": r"acknowledg", "figure": r"(\d+)\s*business days?"},
    "backup retention": {"text": r"backup", "line": r"backup", "figure": r"(\d+)\s*days?"},
    "log retention": {"text": r"log", "line": r"log.*kept|kept.*log|retain", "figure": r"(\d+)\s*days?"},
}


def _figures(text: str, pattern: str) -> list[float]:
    return [float(x) for x in re.findall(pattern, text, flags=re.I)]


def _doc_figures(topic: dict) -> list[dict]:
    hits = []
    for doc in DOCS:
        if doc.name in SKIP_DOCS or not doc.is_file():
            continue
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(topic["line"], line, flags=re.I):
                for fig in _figures(line, topic["figure"]):
                    hits.append({"doc": doc.relative_to(ROOT).as_posix(), "line": n, "figure": fig,
                                 "text": line.strip(" *-")})
    return hits


def review(answers: list[dict], approved: dict[str, set[str]] | None = None) -> dict:
    """`approved` maps answer id to the roles with an authenticated approval (from the release gate).
    Without it, the answer's own `approved_by` is read: fine for the drafter's early pass, never for release."""
    findings = []
    for a in answers:
        if a.get("label") not in ("VERIFIED", "COMMITMENT"):
            continue  # NEEDS-EVIDENCE answers make no claim to contradict
        question, answer = a.get("question") or "", a.get("answer") or ""
        for name, topic in TOPICS.items():
            if not re.search(topic["text"], question, flags=re.I):
                continue
            mine = sorted(set(_figures(answer, topic["figure"])))
            if not mine:
                continue
            seen = set()
            for hit in _doc_figures(topic):
                if hit["figure"] in mine or (hit["doc"], hit["figure"]) in seen:
                    continue
                seen.add((hit["doc"], hit["figure"]))  # one finding per document figure
                owner = "legal" if a.get("label") == "COMMITMENT" else "security-lead"
                accepted = owner in approved.get(str(a["id"]), set()) if approved is not None else a.get("approved_by") == owner
                findings.append({
                    "id": a["id"], "topic": name, "kind": "accepted" if accepted else "conflict",
                    "answer_says": mine, "doc_says": hit["figure"], "where": f"{hit['doc']}:{hit['line']}",
                    "doc_text": hit["text"], "owner": owner,
                    "ask": None if accepted else
                    f"{a['id']} answers {_fmt(mine)} but {hit['doc']}:{hit['line']} says {_fmt([hit['figure']])}. "
                    f"Fix the answer, or ask @{owner} which figure is right.",
                })
    return {"type": "red-team", "checked": len(answers),
            "conflicts": [f for f in findings if f["kind"] == "conflict"],
            "accepted": [f for f in findings if f["kind"] == "accepted"]}


def _fmt(figs) -> str:
    return " / ".join(f"{f:g}" for f in figs)


def markdown(report: dict) -> str:
    out = [f"Red team: {report['checked']} answers checked against every room document",
           f"⚔️ {len(report['conflicts'])} open conflicts   ✔️ {len(report['accepted'])} accepted differences"]
    for f in report["conflicts"]:
        out.append(f"- ⚔️ {f['id']} ({f['topic']}): answer {_fmt(f['answer_says'])}, "
                   f"{f['where']} says {_fmt([f['doc_says']])}: \"{f['doc_text']}\". Owner: @{f['owner']}")
    for f in report["accepted"]:
        out.append(f"- ✔️ {f['id']} ({f['topic']}): answer {_fmt(f['answer_says'])} differs from "
                   f"{f['where']} ({_fmt([f['doc_says']])}); approved by {f['owner']}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("answers")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = review(json.loads(Path(args.answers).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as exc:
        print(f"red_team: {exc}", file=sys.stderr)
        return 2
    print(markdown(report) if args.markdown else json.dumps(report, indent=2))
    return 1 if report["conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main())
