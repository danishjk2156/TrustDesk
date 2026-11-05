"""TrustDesk drift watcher: approved answers decay when the code they cite changes.

Every VERIFIED answer is tied to a control and to the exact lines that proved it. This re-runs
the evidence engine for each one against the repo as it is now and sorts them into:

  OK     same proof, same lines
  MOVED  same proof, but at different line numbers: the claim still holds, refresh the citation
  STALE  the proof is gone or now says the opposite: the answer must not be sent again until the
         Security Lead re-approves it

    python tools/drift_watch.py                          # the answer library
    python tools/drift_watch.py --answers work/answers.json
    python tools/drift_watch.py --markdown               # a summary to post in the room

Exit 0 = nothing stale, 1 = at least one STALE answer, 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

LIBRARY = ROOT / "room-docs" / "answer-library.yaml"


def _text(cite: str) -> str:
    """The matched text of a `path:line  text` citation, without its line number."""
    m = re.match(r"^([\w./-]+):\d+  (.+)$", cite)
    return f"{m.group(1)}  {m.group(2).strip()}" if m else cite


def check_entry(entry: dict, repo: Path) -> dict:
    ident = entry.get("id") or entry.get("question", "")[:60]
    now = search.check(entry["control"], repo)
    base = {"id": ident, "control": entry["control"], "question": entry.get("question"),
            "approved_evidence": entry.get("evidence", [])}
    if now["status"] != "found":
        return {**base, "status": "STALE", "now": now["status"], "now_evidence": now.get("evidence", []),
                "reason": now.get("reason") or f"the evidence engine now says {now['status']}"}
    was, is_ = entry.get("evidence", []), now["evidence"]
    if was == is_:
        return {**base, "status": "OK"}
    if sorted(map(_text, was)) == sorted(map(_text, is_)):
        return {**base, "status": "MOVED", "now_evidence": is_, "now_permalinks": now.get("permalinks"),
                "reason": "same proof at different line numbers"}
    return {**base, "status": "STALE", "now": "found", "now_evidence": is_,
            "reason": "the cited lines changed; the proof no longer matches what was approved"}


def watch(entries: list[dict], repo: Path = search.DEFAULT_REPO) -> dict:
    checked = [check_entry(e, repo) for e in entries if e.get("label") == "VERIFIED" and e.get("control")]
    counts = {s: sum(1 for c in checked if c["status"] == s) for s in ("OK", "MOVED", "STALE")}
    return {"type": "drift", "checked": len(checked), "counts": counts,
            "stale": [c for c in checked if c["status"] == "STALE"],
            "moved": [c for c in checked if c["status"] == "MOVED"]}


def markdown(report: dict) -> str:
    c = report["counts"]
    lines = [f"TrustDesk drift check: {report['checked']} approved answers re-proven against the code now",
             f"✅ {c['OK']} OK   ↪️ {c['MOVED']} MOVED   🔴 {c['STALE']} STALE"]
    if report["stale"]:
        lines += ["", "🔴 STALE (do not send again until re-approved):"]
        for s in report["stale"]:
            was = s["approved_evidence"][0] if s["approved_evidence"] else "(none)"
            now = s["now_evidence"][0] if s.get("now_evidence") else "(no match)"
            lines.append(f"- {s['id']} · {s['question']}\n  approved: {was}\n  now: {now} ({s['reason']})")
    if report["moved"]:
        lines += ["", "↪️ MOVED (claim still holds; citation refreshed on the next package):"]
        lines += [f"- {m['id']} · {m['approved_evidence'][0]} → {m['now_evidence'][0]}" for m in report["moved"]]
    return "\n".join(lines)


def load(args) -> list[dict]:
    if args.answers:
        return json.loads(Path(args.answers).read_text(encoding="utf-8"))
    lib = yaml.safe_load(Path(args.library).read_text(encoding="utf-8")) or {}
    entries = lib.get("answers") or []
    for i, e in enumerate(entries, start=1):
        e.setdefault("id", f"L{i}")  # library entries have no question ID; number them
    return entries


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", default=str(LIBRARY))
    ap.add_argument("--answers", help="an answers.json instead of the library")
    ap.add_argument("--repo", type=Path, default=search.DEFAULT_REPO)
    ap.add_argument("--markdown", action="store_true", help="print a room summary instead of JSON")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    try:
        report = watch(load(args), args.repo.resolve())
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"drift_watch: {exc}", file=sys.stderr)
        return 2
    print(markdown(report) if args.markdown else json.dumps(report, indent=2))
    return 1 if report["stale"] else 0


if __name__ == "__main__":
    sys.exit(main())
