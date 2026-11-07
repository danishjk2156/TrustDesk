"""Scrub answers before they enter the shared answer library.

The library is reused across customers, so an entry must carry nothing about the deal it came from:
no customer names, e-mail addresses, IP addresses or foreign URLs, and only the fields the library
defines. `source_deal` is replaced by a one-way label.

    python tools/scrub.py entries.json --customer "Acme Corp" --customer acme [--out clean.json]

Exit 0 = clean entries printed, 1 = at least one entry refused (nothing printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ALLOWED = ("question", "answer", "label", "control", "evidence", "approved_by", "approved_at", "source_deal")
LABELS = {"VERIFIED", "COMMITMENT"}
EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")  # not `action@0.24.0`
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
URL = re.compile(r"https?://[^\s)\"']+", re.I)
OWN_DOMAINS = ("acmecloud.example",)  # the vendor's own mailboxes (e.g. security@) are public by design
OWN_URL = re.compile(r"^https://github\.com/")  # permalinks into the product repo carry no customer data


def anonymise_deal(deal_id: str) -> str:
    return "deal-" + hashlib.sha256(deal_id.encode("utf-8")).hexdigest()[:8]


def scrub(entry: dict, customers: list[str]) -> tuple[dict | None, list[str]]:
    """(clean entry, problems). A problem means the entry must not be added to the library."""
    problems = []
    if entry.get("label") not in LABELS:
        problems.append(f"label {entry.get('label')!r} is not allowed in the library")
    for field in ("question", "answer", "evidence"):
        text = json.dumps(entry.get(field), ensure_ascii=False)
        if any(not m.group(0).lower().endswith(tuple("@" + d for d in OWN_DOMAINS)) for m in EMAIL.finditer(text)):
            problems.append(f"{field} contains an e-mail address")
        if IPV4.search(text):
            problems.append(f"{field} contains an IP address")
        if any(not OWN_URL.match(u) for u in URL.findall(text)):
            problems.append(f"{field} contains a URL that is not a product-repo link")
        for name in customers:
            if name.strip() and re.search(re.escape(name.strip()), text, re.I):
                problems.append(f"{field} names the customer {name!r}")
    if problems:
        return None, problems
    clean = {k: entry[k] for k in ALLOWED if k in entry}
    if "source_deal" in clean:
        clean["source_deal"] = anonymise_deal(str(clean["source_deal"]))
    return clean, []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entries")
    ap.add_argument("--customer", action="append", default=[], help="a name the customer is known by (repeatable)")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    entries = json.loads(Path(args.entries).read_text(encoding="utf-8"))
    clean, refused = [], False
    for i, entry in enumerate(entries):
        c, problems = scrub(entry, args.customer)
        for p in problems:
            print(f"entry {i} ({entry.get('question', '')[:50]!r}): {p}", file=sys.stderr)
        refused |= bool(problems)
        clean.append(c)
    if refused:
        return 1
    text = json.dumps(clean, indent=2, ensure_ascii=False)
    Path(args.out).write_text(text, encoding="utf-8") if args.out else print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
