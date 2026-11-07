"""Bind every VERIFIED answer to evidence that proves exactly what the answer says.

A real `file:line` is not enough: it could be an unrelated line, or stale, or only part of a larger
claim. For each factual claim this re-runs the deterministic verifier and requires:

  1. the claimed control is one the engine knows, and is relevant to the question being answered
  2. the verifier says `found` for that control against the code as it is now
  3. every citation on the claim is one the verifier produced for that control (no orphan citations)
  4. every concrete number in the answer comes from the evidence, the question, or a sentence
     explicitly marked "Per policy (document): ..."

Nothing here trusts the answer's own words; it is all recomputed from the repo.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

# A question must mention every group (any one word of a group) before a control may answer it.
TOPICS: dict[str, tuple[str, ...]] = {
    "db_encryption_at_rest": (r"encrypt|at rest", r"database|\bdb\b|rds|customer data|data"),
    "kms_key_rotation": (r"key", r"rotat"),
    "storage_encryption_all_buckets": (r"encrypt|at rest", r"storage|bucket|s3|object"),
    "tls_in_transit": (r"tls|ssl|in transit|transit|https",),
    "https_redirect": (r"redirect",),
    "hsts": (r"hsts|strict-transport",),
    "db_tls_required": (r"encrypt|tls|ssl", r"database|\bdb\b|rds"),
    "mfa_admin": (r"mfa|multi-?factor|two-?factor|2fa", r"admin|privileg"),
    "mfa_all_users": (r"mfa|multi-?factor|two-?factor|2fa", r"all|every|workforce|employee|user"),
    "sso": (r"sso|single sign|saml|oidc|identity provider",),
    "password_length": (r"password", r"length|characters|minimum"),
    "password_reuse": (r"password", r"reuse|re-use|history|previous"),
    "session_timeout": (r"session|time out|timeout|inactiv|idle",),
    "least_privilege": (r"least[- ]privilege|read-only|need[- ]to[- ]know|role",),
    "code_review_required": (r"review|peer|approv", r"code|change|merge|pull request"),
    "audit_logging": (r"audit|log", r"admin|trail|region|action"),
    "log_integrity": (r"log", r"integrity|tamper|validat|immutab"),
    "log_retention": (r"log", r"retain|retention|kept|keep|months|year|days"),
    "network_flow_logs": (r"flow log|network log|vpc",),
    "db_backups": (r"backup",),
    "multi_az": (r"availability zone|multi-?az|redundan|highly available|failover",),
    "deletion_protection": (r"delet|accidental", r"database|\bdb\b|rds|protect"),
    "storage_versioning": (r"version",),
    "storage_public_access_blocked": (r"public", r"bucket|storage|s3|access"),
    "waf": (r"waf|firewall",),
    "db_not_public": (r"database|\bdb\b|rds", r"public|internet|isolat|expos"),
    "db_network_restricted": (r"database|\bdb\b|rds", r"restrict|only|limit|network|access"),
    "sast": (r"sast|static (application )?(security )?(testing|analysis)|code scanning|codeql",),
    "dependency_scanning": (r"dependenc|third[- ]party|supply chain",),
    "secrets_scanning": (r"secret|credential|key", r"scan|commit|detect|leak"),
    "container_scanning": (r"container|image", r"scan|vulnerab"),
    "vulnerability_disclosure": (r"disclos|report", r"vulnerab|security"),
}

NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
NUMBER = re.compile(r"\b(\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")\b", re.I)
POLICY_SENTENCE = re.compile(r"^\s*per policy\b", re.I)


def relevant(control: str, question: str) -> bool:
    groups = TOPICS.get(control)
    return bool(groups) and all(re.search(g, question, re.I) for g in groups)


def _normal(token: str) -> str:
    return NUMBER_WORDS.get(token.lower(), token)


def _numbers(text: str) -> set[str]:
    return {_normal(m.group(1)) for m in NUMBER.finditer(text)}


def unsupported_numbers(answer: str, question: str, facts: list[dict], cites: list[str]) -> list[str]:
    """Numbers the answer asserts that neither the evidence nor the question contains."""
    claimed = "".join(s for s in re.split(r"(?<=[.!?])\s+", answer) if not POLICY_SENTENCE.match(s))
    allowed = _numbers(question)
    for fact in facts:
        allowed |= {str(fact["value"])} if isinstance(fact["value"], int) and not isinstance(fact["value"], bool) else set()
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", str(fact["value"])))
    for cite in cites:
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", cite.split("  ", 1)[-1]))
    allowed |= {str(len(facts)), str(len({f["subject"] for f in facts})), str(len({f["file"] for f in facts}))}
    return sorted({_normal(m.group(1)) for m in NUMBER.finditer(claimed)} - allowed)


def claims_of(answer: dict) -> list[dict]:
    """The answer's claims: explicit `claims`, or the single control/evidence pair of a simple answer."""
    if isinstance(answer.get("claims"), list):
        return answer["claims"]
    return [{"control": answer.get("control"), "evidence": answer.get("evidence") or []}]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_answer(answer: dict, repo: Path) -> tuple[list[str], dict]:
    """(problems, proof). The proof is what the verifier found now; the digest of an answer covers it."""
    problems: list[str] = []
    proofs: list[dict] = []
    question = str(answer.get("question") or "")
    claims = claims_of(answer)
    if not claims or not any(c.get("control") for c in claims):
        return ["VERIFIED without a control to prove"], {"claims": []}
    all_cites: list[str] = []
    all_facts: list[dict] = []
    for claim in claims:
        control, cites = claim.get("control"), claim.get("evidence") or []
        if control not in search.RULES:
            problems.append(f"unknown control {control!r}; the engine cannot prove it")
            continue
        if not relevant(control, question):
            problems.append(f"control {control} is not relevant to this question")
        if not cites:
            problems.append(f"claim {control} has no citation")
        result = search.check(control, repo)
        if result["status"] != "found":
            problems.append(f"claim {control} is {result['status']} in the code now: {result.get('reason') or 'no proof'}")
            continue
        current = set(result["evidence"])
        for cite in cites:
            if cite not in current:
                problems.append(f"citation is not evidence for {control}: {cite!r}")
        all_cites += cites
        all_facts += result["facts"]
        proofs.append({"control": control, "evidence": sorted(current), "facts": result["facts"],
                       "files": {f: file_hash(repo.parent / f) for f in sorted({x["file"] for x in result["facts"]})}})
    orphans = [c for c in (answer.get("evidence") or []) if c not in all_cites]
    if orphans:
        problems.append(f"citation not attached to any claim: {orphans[0]!r}")
    extra = unsupported_numbers(str(answer.get("answer") or ""), question, all_facts, all_cites)
    if extra:
        problems.append(f"answer states {', '.join(extra)}, which the evidence does not support "
                        f'(put policy figures in a "Per policy (document): ..." sentence)')
    return problems, {"claims": proofs}
