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
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

ALLOWED = ("question", "answer", "label", "control", "evidence", "approved_by", "approved_at", "source_deal")
LABELS = {"VERIFIED", "COMMITMENT"}
ROLES = {"security-lead", "legal"}
KEY_ENV = "TRUSTDESK_SCRUB_KEY"  # secret that keys the source_deal label; never committed
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})\Z")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
OWN_DOMAINS = ("acmecloud.example",)  # the vendor's own mailboxes (e.g. security@) are public by design


def anonymise_deal(deal_id: str) -> str:
    """A label that cannot be reversed to the deal. Keyed (stable, groups one deal's entries) when
    TRUSTDESK_SCRUB_KEY is set; otherwise random, so nothing links the entry back to its deal."""
    key = os.environ.get(KEY_ENV)
    if key:
        return "deal-" + hmac.new(key.encode("utf-8"), deal_id.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
    return "deal-" + secrets.token_hex(6)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif value is not None:
        yield str(value)


FILLERS = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800", "\u180e", "\u034f"}  # render as blank but are letters or marks


def _is_hidden(c: str) -> bool:
    return (unicodedata.category(c) in ("Cc", "Cf", "Mn", "Me") or c in FILLERS) and c not in "\n\t"


def _hidden_chars(value) -> bool:
    return any(_is_hidden(c) for t in _strings(value) for c in t)


def _fold(text: str) -> str:
    """NFKD, then drop control, invisible, filler and combining characters, so neither a name nor an e-mail can be
    disguised by marks or fillers. Applied to the text AND the customer names before they are compared."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not _is_hidden(c))


def _visible(value) -> str:
    """The text as a reader sees it, each string folded BEFORE any serialising."""
    return "\n".join(_fold(t) for t in _strings(value))


TRAILING = ".,;:)\"'>]"
URL_JUNK = set("<>[]{}|\\^`,;\"'")


def _bad_email(text: str) -> bool:
    """True if any token looks like an e-mail address and is not plain ASCII at one of our own domains.
    A token is e-mail-like when it has an @ and a host that ends in letters (`.com`) or holds non-ASCII
    characters; `codeql-action@v3` and `trivy-action@0.24.0` are not."""
    for token in text.split():
        if "@" not in token:
            continue
        host = token.rsplit("@", 1)[1].rstrip(TRAILING)
        if host.isascii() and not re.search(r"\.[A-Za-z]{2,}\Z", host):
            continue
        local = token.rsplit("@", 1)[0]
        if not (token.isascii() and host.lower() in OWN_DOMAINS and local):
            return True
    return False


def _bad_url(text: str) -> bool:
    """True if any `scheme://` occurrence is not a plain-ASCII https://github.com/ link, judged one by one."""
    for m in re.finditer(r"://", text):
        start = max(text.rfind(c, 0, m.start()) for c in " \t\n(\"'<[") + 1
        token = text[start:].split()[0].rstrip(TRAILING)
        parts = urlsplit(token)
        if not token.isascii() or parts.scheme != "https" or parts.netloc != "github.com" or "://" in token[m.end() - start:] \
                or URL_JUNK & set(token):
            return True
    return False


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
        if _hidden_chars(entry[field]):
            problems.append(f"{field} contains control or invisible characters")
        text = _visible(entry[field])
        raw = "\n".join(_strings(entry[field]))
        # Allowlists judge the RAW text as well as the folded text, and only plain ASCII passes, so a
        # look-alike host (full-width, composed) cannot fold into an allowed one.
        if _bad_email(raw) or _bad_email(text):
            problems.append(f"{field} contains an e-mail address")
        if IPV4.search(text) or IPV4.search(raw):
            problems.append(f"{field} contains an IP address")
        if _bad_url(raw) or _bad_url(text):
            problems.append(f"{field} contains a URL that is not a product-repo link")
        for name in customers:
            if name.strip() and re.search(re.escape(_fold(name.strip())), text, re.I):
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
