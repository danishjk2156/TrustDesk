"""TrustDesk evidence engine: prove security claims against the product repo.

Deterministic by design: every control maps to a fixed search rule over the repo, so the
same claims always get the same citations. No model is involved; the evidence-checker agent
only runs this script and posts its JSON.

Input  (file path or stdin):  {"type": "claims", "claims": [{"id", "control", "claim"}]}
Output (stdout):              {"type": "evidence", "results": [...]}

Each result is one of (results with evidence also carry "permalinks": GitHub links pinned to the
current commit, one per citation, or null for a file with uncommitted changes):
  {"id", "control", "status": "found",        "evidence": ["<path>:<line>  <text>", ...]}
  {"id", "control", "status": "missing",      "reason": "..."}
  {"id", "control", "status": "contradicted", "evidence": [...], "reason": "..."}

    python agents/evidence/search.py claims.json
    python agents/evidence/search.py --list           # control tags, for the drafter
    python agents/evidence/search.py --repo PATH ...  # search another repo
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / "acmecloud"


@dataclass(frozen=True)
class Rule:
    desc: str
    glob: str
    pattern: str
    contradiction: str | None = None  # a match here means the code says the opposite
    require_all: tuple[str, ...] = field(default_factory=tuple)  # extra patterns, same files


# Controls the repo can prove. Keys are the control tags the drafter must use.
RULES: dict[str, Rule] = {
    # Encryption
    "db_encryption_at_rest": Rule("Database storage encrypted with KMS", "terraform/*.tf", r"storage_encrypted\s*=\s*true"),
    "kms_key_rotation": Rule("KMS keys rotate automatically", "terraform/*.tf", r"enable_key_rotation\s*=\s*true"),
    "tls_in_transit": Rule("Public endpoints use TLS 1.2+", "terraform/*.tf", r'ssl_policy\s*=\s*"ELBSecurityPolicy-TLS13-1-2'),
    "https_redirect": Rule("Plain HTTP redirects to HTTPS", "terraform/*.tf", r'status_code\s*=\s*"HTTP_301"', require_all=(r'type\s*=\s*"redirect"',)),
    "hsts": Rule("HSTS header set", "configs/*.conf", r"Strict-Transport-Security"),
    "db_tls_required": Rule("Database requires TLS connections", "terraform/*.tf", r'name\s*=\s*"rds\.force_ssl"'),
    # Identity and access
    "mfa_admin": Rule("MFA required for admin actions", "iam/*.json", r'"aws:MultiFactorAuthPresent"\s*:\s*"false"'),
    "mfa_all_users": Rule("MFA enforced for every workforce user", "configs/*.yaml", r"enforced_for_all_users:\s*true",
                          contradiction=r"enforced_for_all_users:\s*false"),
    "sso": Rule("Workforce SSO", "configs/*.yaml", r"provider:\s*okta"),
    "password_length": Rule("Minimum password length 14", "terraform/*.tf", r"minimum_password_length\s*=\s*(1[4-9]|[2-9]\d)"),
    "password_reuse": Rule("Password reuse prevented", "terraform/*.tf", r"password_reuse_prevention\s*=\s*\d+"),
    "session_timeout": Rule("Session timeout configured", "configs/*.yaml", r"session_timeout_minutes:\s*\d+"),
    "least_privilege": Rule("Read-only role for auditors", "terraform/*.tf", r"policy/ReadOnlyAccess"),
    "code_review_required": Rule("Reviewed pull requests required on main", ".github/settings.yml", r"required_approving_review_count:\s*[1-9]"),
    # Logging and monitoring
    "audit_logging": Rule("Multi-region audit trail", "terraform/*.tf", r"is_multi_region_trail\s*=\s*true"),
    "log_integrity": Rule("Audit log file validation", "terraform/*.tf", r"enable_log_file_validation\s*=\s*true"),
    "log_retention": Rule("Logs kept for a year", "terraform/*.tf", r"retention_in_days\s*=\s*(36[5-9]|3[7-9]\d|[4-9]\d\d|\d{4,})"),
    "network_flow_logs": Rule("VPC flow logs", "terraform/*.tf", r'resource\s+"aws_flow_log"'),
    # Resilience
    "db_backups": Rule("Automated database backups", "terraform/*.tf", r"backup_retention_period\s*=\s*[1-9]\d*"),
    "multi_az": Rule("Database runs multi-AZ", "terraform/*.tf", r"multi_az\s*=\s*true"),
    "deletion_protection": Rule("Database deletion protection", "terraform/*.tf", r"deletion_protection\s*=\s*true"),
    "storage_versioning": Rule("Object versioning on storage", "terraform/*.tf", r'status\s*=\s*"Enabled"'),
    # Network
    "storage_public_access_blocked": Rule("Public access blocked on storage", "terraform/*.tf", r"block_public_acls\s*=\s*true"),
    "waf": Rule("Web application firewall on the public load balancer", "terraform/*.tf", r'resource\s+"aws_wafv2_web_acl_association"'),
    "db_not_public": Rule("Database not reachable from the internet", "terraform/*.tf", r"publicly_accessible\s*=\s*false"),
    "db_network_restricted": Rule("Database accepts only the app tier", "terraform/*.tf", r"security_groups\s*=\s*\[aws_security_group\.app\.id\]"),
    # Secure development
    "sast": Rule("Static analysis in CI", ".github/workflows/*.yml", r"github/codeql-action/analyze"),
    "dependency_scanning": Rule("Dependency updates and alerts", ".github/dependabot.yml", r"package-ecosystem:"),
    "secrets_scanning": Rule("Secret scanning in CI", ".github/workflows/*.yml", r"gitleaks/gitleaks-action"),
    "container_scanning": Rule("Container image scanning in CI", ".github/workflows/*.yml", r"aquasecurity/trivy-action"),
    "vulnerability_disclosure": Rule("Public vulnerability reporting channel", "SECURITY.md", r"security@[\w.-]+"),
}

# Controls checked by code rather than one pattern.
SPECIAL = {"storage_encryption_all_buckets": "Every S3 bucket has server-side encryption"}

# Controls a repo cannot prove; listed so the drafter tags them and the checker says why.
NOT_IN_CODE = {
    "backup_restore_testing": "Backup restores are tested",
    "penetration_testing": "Independent penetration test",
    "dr_testing": "Disaster recovery plan is tested",
    "endpoint_protection": "Endpoint protection on staff devices",
    "data_deletion": "Customer data deleted on request or contract end",
    "background_checks": "Background checks for staff",
    "soc2_report": "Current SOC 2 Type II report",
}


def _line_hits(repo: Path, glob: str, pattern: str) -> list[tuple[Path, int, str]]:
    rx = re.compile(pattern)
    hits = []
    for path in sorted(repo.glob(glob)):
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if rx.search(line):
                hits.append((path, n, line.strip()))
    return hits


def _cite(repo: Path, hits) -> list[str]:
    base = repo.parent
    return [f"{p.relative_to(base).as_posix()}:{n}  {text}" for p, n, text in hits]


def _git(*args: str, cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


@functools.lru_cache(maxsize=None)
def _github_base(repo: Path) -> tuple[str, str, Path] | None:
    """(https://github.com/owner/name, commit sha, git top-level) for the repo, if it has one."""
    top = _git("rev-parse", "--show-toplevel", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo)
    url = _git("remote", "get-url", "origin", cwd=repo)
    if not (top and sha and url):
        return None
    m = re.match(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}", sha, Path(top)


def _permalinks(repo: Path, hits) -> list[str | None] | None:
    """Pin each hit to the commit, so a citation keeps pointing at what was proven."""
    base = _github_base(repo)
    if not base:
        return None
    url, sha, top = base
    links = []
    for p, n, _ in hits:
        rel = p.resolve().relative_to(top.resolve()).as_posix()
        links.append(f"{url}/blob/{sha}/{rel}#L{n}" if _committed(top, sha, rel) else None)
    return links


@functools.lru_cache(maxsize=None)
def _committed(top: Path, sha: str, rel: str) -> bool:
    """True when the file is tracked and has no uncommitted changes, so the link shows these lines."""
    unchanged = _git("diff", "--quiet", "HEAD", "--", rel, cwd=top) is not None
    tracked = _git("ls-files", "--error-unmatch", rel, cwd=top) is not None
    return unchanged and tracked


def _with_evidence(repo: Path, hits, **fields) -> dict:
    result = {**fields, "evidence": _cite(repo, hits)}
    links = _permalinks(repo, hits)
    if links is not None:
        result["permalinks"] = links
    return result


def _check_rule(repo: Path, rule: Rule) -> dict:
    bad = _line_hits(repo, rule.glob, rule.contradiction) if rule.contradiction else []
    if bad:
        return _with_evidence(repo, bad, status="contradicted",
                              reason=f"The code says the opposite of: {rule.desc.lower()}")
    hits = _line_hits(repo, rule.glob, rule.pattern)
    for extra in rule.require_all:
        if not _line_hits(repo, rule.glob, extra):
            hits = []
    if hits:
        return _with_evidence(repo, hits[:3], status="found")
    return {"status": "missing", "reason": f"No match in {rule.glob} for: {rule.desc.lower()}"}


def _check_bucket_encryption(repo: Path) -> dict:
    """Every aws_s3_bucket needs an encryption configuration pointing at it."""
    buckets, encrypted = {}, set()
    for path in sorted(repo.glob("terraform/*.tf")):
        current = None
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.match(r'\s*resource\s+"([\w]+)"\s+"(\w+)"', line)
            if m:
                current = m.group(1)
                if current == "aws_s3_bucket":
                    buckets[m.group(2)] = (path, n, line.strip())
                continue
            ref = re.search(r"bucket\s*=\s*aws_s3_bucket\.(\w+)\.id", line)
            if ref and current == "aws_s3_bucket_server_side_encryption_configuration":
                encrypted.add(ref.group(1))
    if not buckets:
        return {"status": "missing", "reason": "No S3 buckets defined in terraform/"}
    unencrypted = [name for name in buckets if name not in encrypted]
    if unencrypted:
        return _with_evidence(repo, [buckets[n] for n in unencrypted], status="contradicted",
                              reason=f"{len(unencrypted)} of {len(buckets)} S3 buckets have no encryption "
                                     f"configuration: " + ", ".join(unencrypted))
    return _with_evidence(repo, list(buckets.values()), status="found")


def check(control: str, repo: Path = DEFAULT_REPO) -> dict:
    if control in RULES:
        return _check_rule(repo, RULES[control])
    if control == "storage_encryption_all_buckets":
        return _check_bucket_encryption(repo)
    if control in NOT_IN_CODE:
        return {"status": "missing", "reason": f"Not provable from code: {NOT_IN_CODE[control].lower()}; needs a document or record"}
    return {"status": "missing", "reason": f"Unknown control tag {control!r}; run search.py --list"}


def run(claims_msg: dict, repo: Path = DEFAULT_REPO) -> dict:
    if claims_msg.get("type") != "claims":
        raise ValueError('expected {"type": "claims", "claims": [...]}')
    results = []
    for c in claims_msg.get("claims", []):
        results.append({"id": c["id"], "control": c.get("control"), **check(c.get("control", ""), repo)})
    return {"type": "evidence", "results": results}


def controls() -> dict[str, str]:
    return {**{k: r.desc for k, r in RULES.items()}, **SPECIAL, **NOT_IN_CODE}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("claims", nargs="?", help="claims JSON file (default: stdin)")
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--list", action="store_true", help="print control tags and exit")
    args = ap.parse_args(argv)
    if args.list:
        for tag, desc in controls().items():
            print(f"{tag:32} {desc}")
        return 0
    raw = Path(args.claims).read_text(encoding="utf-8") if args.claims else sys.stdin.read()
    print(json.dumps(run(json.loads(raw), args.repo.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
