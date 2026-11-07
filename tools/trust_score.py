"""Deal trust score: one line that says how much of a questionnaire is actually proven.

    python tools/trust_score.py work/answers.json
    python tools/trust_score.py work/answers.json --json
    python tools/trust_score.py work/answers.json --gate-report gate.json   # counts authenticated approvals

Score = share of factual answers that are VERIFIED (proven from code), with the other signals
reported beside it rather than folded into one opaque number.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import drift_watch  # noqa: E402
import red_team  # noqa: E402


def score(answers: list[dict], approvals: dict | None = None) -> dict:
    """`approvals` is the release gate's map of authenticated approvals; without it nothing counts as approved."""
    approvals = approvals or {}
    total = len(answers)
    by = {k: sum(1 for a in answers if a.get("label") == k) for k in ("VERIFIED", "NEEDS-EVIDENCE", "COMMITMENT")}
    factual = by["VERIFIED"] + by["NEEDS-EVIDENCE"]
    contradicted = sum(1 for a in answers if a.get("label") == "NEEDS-EVIDENCE" and a.get("control")
                       and drift_watch.search.check(a["control"])["status"] == "contradicted")
    approved = sum(1 for a in answers if str(a.get("id")) in approvals)
    drift = drift_watch.watch(answers)
    conflicts = len(red_team.review(answers, {k: {v["role"]} for k, v in approvals.items()})["conflicts"])
    return {
        "verified_pct": round(100 * by["VERIFIED"] / factual) if factual else 0,
        "verified": by["VERIFIED"], "factual": factual, "needs_evidence": by["NEEDS-EVIDENCE"],
        "contradicted": contradicted, "commitments": by["COMMITMENT"], "approved": approved, "total": total,
        "stale": drift["counts"]["STALE"], "open_conflicts": conflicts,
    }


def line(s: dict) -> str:
    return (f"Trust score {s['verified_pct']}% proven from code ({s['verified']}/{s['factual']} factual answers) · "
            f"{s['contradicted']} policy claims contradicted · {s['stale']} stale · {s['open_conflicts']} open conflicts · "
            f"{s['approved']}/{s['total']} approved")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("answers")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gate-report", help="check_gates.py output; only its authenticated approvals count")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    gate = json.loads(Path(args.gate_report).read_text(encoding="utf-8")) if args.gate_report else {}
    s = score(json.loads(Path(args.answers).read_text(encoding="utf-8")), gate.get("approvals"))
    print(json.dumps(s, indent=2) if args.json else line(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
