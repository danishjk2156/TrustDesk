"""Authenticated, version-bound approvals.

An answer is released only on an approval event that

  * comes from a person in the approver roster (`room-docs/approvers.yaml`), in the role the label needs
    (security-lead for facts, legal for COMMITMENT),
  * is authenticated outside the model: an Ed25519 signature made with that person's private key, or a
    GitHub PR review fetched from GitHub and matched to their login,
  * names the digest of the exact answer, its claims, the evidence the verifier finds now (resource,
    value, file, line and file hash) and, for a COMMITMENT, the policy documents it rests on.

Change any of those and the digest changes, so the old approval no longer matches: re-approval is
required automatically. Approvals live in an append-only ledger (`approvals.jsonl`) that no drafting
agent has a key for. The `approved_by` field inside an answer record is never trusted.

    python tools/approvals.py keygen --out alice.key
    python tools/approvals.py sign   --key alice.key --approver alice --role security-lead --deal acme \\
                                     --answers work/acme/answers.json [--ids Q1,Q2] [--yes]
    python tools/approvals.py revoke --key alice.key --approver alice --role security-lead --deal acme \\
                                     --answers work/acme/answers.json --ids Q1
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import claims  # noqa: E402
import red_team  # noqa: E402

DEAL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CLOCK_SKEW = timedelta(minutes=5)
MAX_AGE = timedelta(days=30)
ROSTER = ROOT / "room-docs" / "approvers.yaml"
_REPO = re.compile(r"^[A-Za-z0-9_-][\w.-]*/[A-Za-z0-9_-][\w.-]*$")  # no segment made only of dots
_SHA = re.compile(r"^[0-9a-f]{40}$")


def required_role(label: str) -> str:
    return "legal" if label == "COMMITMENT" else "security-lead"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# --- digest -------------------------------------------------------------------------------------

def policy_hashes() -> dict[str, str]:
    """Hash of every document a promise may rest on, so editing a policy invalidates its approvals."""
    return {d.relative_to(ROOT).as_posix(): hashlib.sha256(d.read_bytes()).hexdigest()
            for d in red_team.DOCS if d.is_file() and d.name not in red_team.SKIP_DOCS}


def proof_of(answer: dict, repo: Path) -> tuple[list[str], dict]:
    """(problems, proof) for any label. VERIFIED answers are fully re-proven; others record the verdict."""
    if answer.get("label") == "VERIFIED":
        return claims.verify_answer(answer, repo)
    proof: dict = {"claims": []}
    for c in claims.claims_of(answer):
        if c.get("control") and c["control"] in claims.search.RULES:
            r = claims.search.check(c["control"], repo)
            proof["claims"].append({"control": c["control"], "status": r["status"], "reason": r.get("reason"),
                                    "facts": r.get("facts", [])})
    return [], proof


def digest_of(answer: dict, proof: dict) -> str:
    payload = {"id": answer.get("id"), "question": answer.get("question"), "answer": answer.get("answer"),
               "label": answer.get("label"), "proof": proof,
               "claims": [{"control": c.get("control"), "evidence": sorted(c.get("evidence") or [])}
                          for c in claims.claims_of(answer)],
               "policy": policy_hashes() if answer.get("label") == "COMMITMENT" else {}}
    return hashlib.sha256(canonical(payload)).hexdigest()


# --- keys, roster, signatures -------------------------------------------------------------------

def keygen() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def sign(private_b64: str, payload: dict) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(key.sign(canonical(payload))).decode()


def verify_signature(public_b64: str, payload: dict, signature_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(base64.b64decode(signature_b64), canonical(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def load_github_repo(path: Path = ROSTER) -> str | None:
    """The one repository whose PR reviews count as approvals (`github_repo: owner/name` in the roster)."""
    repo = (yaml.safe_load(path.read_bytes()) or {}).get("github_repo")
    return repo if isinstance(repo, str) and _REPO.match(repo) else None


def load_roster(path: Path = ROSTER) -> tuple[dict[str, dict], str]:
    """({approver id: {roles, github, public_key}}, sha256 of the file)."""
    raw = path.read_bytes()
    doc = yaml.safe_load(raw) or {}
    roster = {}
    for entry in doc.get("approvers") or []:
        roles = {r for r in entry.get("roles") or [] if r in ("security-lead", "legal")}
        roster[str(entry["id"])] = {"roles": roles, "github": entry.get("github"), "public_key": entry.get("public_key")}
    return roster, hashlib.sha256(raw).hexdigest()


# --- ledger entries -----------------------------------------------------------------------------

def _signed_fields(kind: str, deal: str, answer_id: str, digest: str, role: str, approver: str, at: str) -> dict:
    return {"type": kind, "deal": deal, "answer_id": answer_id, "digest": digest, "role": role, "approver": approver,
            "at": at}


def make_entry(private_b64: str, kind: str, deal: str, answer_id: str, digest: str, role: str, approver: str,
               at: datetime | None = None) -> dict:
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    fields = _signed_fields(kind, deal, answer_id, digest, role, approver, stamp)
    return {**fields, "provider": "signed", "signature": sign(private_b64, fields)}


def append(ledger: Path, entry: dict) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def read_ledger(path: Path) -> tuple[list[dict], list[str]]:
    entries, bad = [], []
    if not path.is_file():
        return entries, bad
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError
            entries.append(entry)
        except ValueError:
            bad.append(f"ledger line {n} is not valid JSON")
    return entries, bad


class GitHubSource:
    """Fetch a PR review and the digest manifest at its commit from GitHub, with the `gh` CLI."""

    def review(self, ref: dict) -> dict:
        out = subprocess.run(["gh", "api", f"repos/{ref['repo']}/pulls/{int(ref['pr'])}/reviews/{int(ref['review_id'])}"],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        r = json.loads(out)
        pr = json.loads(subprocess.run(["gh", "api", f"repos/{ref['repo']}/pulls/{int(ref['pr'])}"],
                                       capture_output=True, text=True, timeout=30, check=True).stdout)
        return {"login": (r.get("user") or {}).get("login"), "state": r.get("state"), "commit_id": r.get("commit_id"),
                "submitted_at": r.get("submitted_at"), "merged": bool(pr.get("merged")), "base": (pr.get("base") or {}).get("ref")}

    def digests(self, ref: dict, deal: str) -> dict:
        path = f"approvals/digests-{deal}.json"
        out = subprocess.run(["gh", "api", f"repos/{ref['repo']}/contents/{path}?ref={ref['commit']}", "--jq", ".content"],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        return json.loads(base64.b64decode(out))




def _parse_time(value) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


@dataclass(frozen=True)
class Approval:
    answer_id: str
    digest: str
    role: str
    approver: str
    at: datetime


def _check_entry(entry: dict, roster: dict, deal: str, github: GitHubSource | None,
                 github_repo: str | None = None) -> tuple[Approval | None, str | None]:
    approver, role = str(entry.get("approver")), entry.get("role")
    person = roster.get(approver)
    if entry.get("deal") != deal:
        return None, f"approval is for deal {entry.get('deal')!r}, not {deal!r}"
    if not person or role not in person["roles"]:
        return None, f"{approver!r} is not in the roster as {role}"
    at = _parse_time(entry.get("at"))
    if entry.get("provider") == "signed":
        if not person["public_key"] or not verify_signature(person["public_key"], _signed_fields(
                entry.get("type"), deal, entry.get("answer_id"), entry.get("digest"), role, approver, entry.get("at")),
                str(entry.get("signature"))):
            return None, f"signature of {approver!r} does not verify"
    elif entry.get("provider") == "github" and entry.get("type") == "approval":
        ref = entry.get("review") or {}
        if github is None:
            return None, "GitHub approvals need online verification (run the gate with --verify-github)"
        if not (_REPO.match(str(ref.get("repo"))) and _SHA.match(str(ref.get("commit"))) and DEAL_ID.match(deal)):
            return None, "malformed GitHub review reference"
        if ref["repo"] != github_repo:
            return None, "GitHub review is not from the repository the roster pins (github_repo)"
        try:
            review = github.review(ref)
            manifest = github.digests(ref, deal)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return None, f"GitHub could not confirm the review: {type(exc).__name__}"
        if review["state"] != "APPROVED" or review["commit_id"] != ref["commit"] or review["login"] != person["github"]:
            return None, "GitHub review is not an approval by that person on that commit"
        if not review.get("merged") or review.get("base") != "main":
            return None, "the reviewed PR is not merged into main"
        if manifest.get(entry.get("answer_id")) != entry.get("digest"):
            return None, "the reviewed commit does not list this digest"
        at = _parse_time(review["submitted_at"])
    else:
        return None, f"unknown approval provider {entry.get('provider')!r}"
    if at is None:
        return None, "approval time is missing or has no timezone"
    return Approval(str(entry.get("answer_id")), str(entry.get("digest")), role, approver, at), None


def valid_approvals(entries: list[dict], roster: dict, deal: str, now: datetime, github: GitHubSource | None = None,
                    max_age: timedelta = MAX_AGE, github_repo: str | None = None) -> tuple[list[Approval], list[str]]:
    """Approvals that verify, are not future-dated or expired, and are not revoked; plus why others failed."""
    good, rejected, revokes = [], [], []
    for entry in entries:
        kind = entry.get("type")
        if kind not in ("approval", "revoke"):
            rejected.append(f"unknown ledger entry type {kind!r}")
            continue
        approval, why = _check_entry(entry, roster, deal, github, github_repo)
        if approval is None:
            rejected.append(f"{entry.get('answer_id')}: {why}")
        elif approval.at > now + CLOCK_SKEW:
            rejected.append(f"{approval.answer_id}: approval is dated in the future")
        elif kind == "revoke":
            revokes.append(approval)
        elif now - approval.at > max_age:
            rejected.append(f"{approval.answer_id}: approval by {approval.approver} expired ({max_age.days} days)")
        else:
            good.append(approval)
    live = [a for a in good if not any(r.answer_id == a.answer_id and r.digest == a.digest and r.at >= a.at for r in revokes)]
    return live, rejected


# --- command line -------------------------------------------------------------------------------

def _digests(answers: list[dict], repo: Path) -> dict[str, str]:
    return {str(a["id"]): digest_of(a, proof_of(a, repo)[1]) for a in answers}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", required=True)
    for name in ("sign", "revoke"):
        sp = sub.add_parser(name)
        sp.add_argument("--key", required=True)
        sp.add_argument("--approver", required=True)
        sp.add_argument("--role", required=True, choices=["security-lead", "legal"])
        sp.add_argument("--deal", required=True)
        sp.add_argument("--answers", required=True)
        sp.add_argument("--ledger")
        sp.add_argument("--ids")
        sp.add_argument("--repo", type=Path, default=claims.search.DEFAULT_REPO)
        sp.add_argument("--yes", action="store_true", help="do not ask before signing")
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        private, public = keygen()
        out = Path(args.out)
        out.write_text(private, encoding="utf-8")
        out.chmod(0o600)
        print(f"private key written to {out} (keep it off shared machines)\npublic_key: {public}")
        return 0
    if not DEAL_ID.match(args.deal):
        print("deal id must be lowercase letters, digits, - or _", file=sys.stderr)
        return 2
    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    wanted = set(args.ids.split(",")) if args.ids else None
    ledger = Path(args.ledger) if args.ledger else Path(args.answers).with_name("approvals.jsonl")
    digests = _digests(answers, args.repo.resolve())
    for a in answers:
        if wanted is not None and str(a["id"]) not in wanted:
            continue
        if args.cmd == "sign" and required_role(a.get("label")) != args.role:
            print(f"skip {a['id']}: needs {required_role(a.get('label'))}, you are {args.role}")
            continue
        print(f"\n{a['id']} [{a.get('label')}] {a.get('question')}\n  answer:   {a.get('answer')}\n  evidence: {a.get('evidence')}\n  digest:   {digests[str(a['id'])][:16]}")
        if not args.yes and input(f"{args.cmd} this? [y/N] ").strip().lower() != "y":
            continue
        append(ledger, make_entry(Path(args.key).read_text(encoding="utf-8").strip(), "approval" if args.cmd == "sign" else "revoke",
                                  args.deal, str(a["id"]), digests[str(a["id"])], args.role, args.approver))
    print(f"\nledger: {ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
