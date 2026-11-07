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
import hmac
import json
import os
import re
import secrets
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

ALLOWED = ("question", "answer", "label", "control", "evidence", "approved_by", "approved_at", "source_deal")
LABELS = {"VERIFIED", "COMMITMENT"}
ROLES = {"security-lead", "legal"}
KEY_ENV = "TRUSTDESK_SCRUB_KEY"  # secret that keys the source_deal label; never committed
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})\Z")
EMAIL = re.compile(r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")  # not `action@0.24.0`
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
URL = re.compile(r"https?://[^\s)\"']+", re.I)
OWN_DOMAINS = ("acmecloud.example",)  # the vendor's own mailboxes (e.g. security@) are public by design
OWN_URL = re.compile(r"^https://github\.com/")  # permalinks into the product repo carry no customer data


def anonymise_deal(deal_id: str) -> str:
    """A label that cannot be reversed to the deal. Keyed (stable, groups one deal's entries) when
    TRUSTDESK_SCRUB_KEY is set; otherwise random, so nothing links the entry back to its deal."""
    key = os.environ.get(KEY_ENV)
    if key:
        return "deal-" + hmac.new(key.encode("utf-8"), deal_id.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    return "deal-" + secrets.token_hex(6)


def _visible(value) -> str:
    """The text as a reader sees it: NFKC, invisible and control characters removed, so a name cannot be split."""
    text = unicodedata.normalize("NFKC", json.dumps(value, ensure_ascii=False))
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf"))


def scrub(entry: dict, customers: list[str]) -> tuple[dict | None, list[str]]:
    """(clean entry, problems). A problem means the entry must not be added to the library."""
    problems = []
    if entry.get("label") not in LABELS:
        problems.append(f"label {entry.get('label')!r} is not allowed in the library")
    if entry.get("control") not in (None, "other", *search.RULES):  # COMMITMENT entries use "other"
        problems.append(f"control {entry.get('control')!r} is not a known control")
    if entry.get("approved_by") is not None and entry.get("approved_by") not in ROLES:
        problems.append(f"approved_by {entry.get('approved_by')!r} is not a role")
    if entry.get("approved_at") is not None and not TIMESTAMP.match(str(entry.get("approved_at"))):
        problems.append("approved_at is not an ISO timestamp")
    for field in (f for f in ALLOWED if f != "source_deal" and f in entry):
        text = _visible(entry[field])
        if any(not m.group(0).lower().endswith(tuple("@" + d for d in OWN_DOMAINS)) for m in EMAIL.finditer(text)):
            problems.append(f"{field} contains an e-mail address")
        if IPV4.search(text):
            problems.append(f"{field} contains an IP address")
        if any(not OWN_URL.match(u) for u in URL.findall(text)):
            problems.append(f"{field} contains a URL that is not a product-repo link")
        for name in customers:
            if name.strip() and re.search(re.escape(unicodedata.normalize("NFKC", name.strip())), text, re.I):
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
