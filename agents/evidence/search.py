"""TrustDesk evidence engine: prove security claims against the product repo.

Deterministic by design: every control is a fixed check over parsed Terraform, YAML and JSON (not a
text search), so the same claims always get the same citations. No model is involved; the
evidence-checker agent only runs this script and posts its JSON.

Input  (file path or stdin):  {"type": "claims", "claims": [{"id", "control", "claim"}]}
Output (stdout):              {"type": "evidence", "results": [...]}

Each result is one of (results with evidence also carry "permalinks": GitHub links pinned to the
current commit, one per citation, or null for a file with uncommitted changes):
  {"id", "control", "status": "found",        "evidence": ["<path>:<line>  <text>", ...]}
  {"id", "control", "status": "missing",      "reason": "..."}
  {"id", "control", "status": "contradicted", "evidence": [...], "reason": "..."}
  {"id", "control", "status": "unverifiable", "evidence": [...], "reason": "..."}
      a variable, module, count/for_each or unreadable file stands between the code and an answer;
      this is never a pass
Results with evidence also carry "facts": the resource, attribute, value, file and line behind each
citation.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls_cfg import CONTROLS as CFG_CONTROLS  # noqa: E402
from controls_tf import CONTROLS as TF_CONTROLS  # noqa: E402
from loaders import Unresolved  # noqa: E402
from product import Product  # noqa: E402
from verdict import Control, Fact, Verdict  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / "acmecloud"

# Controls the repo can prove. Keys are the control tags the drafter must use.
RULES: dict[str, Control] = {**TF_CONTROLS, **CFG_CONTROLS}

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


def _cite(repo: Path, facts: tuple[Fact, ...]) -> list[str]:
    """`path:line  text` for each fact, with the text read from the line it cites."""
    base = repo.parent
    seen, out = set(), []
    for f in facts:
        text = f.file.read_text(encoding="utf-8").splitlines()[f.line - 1].strip()
        cite = f"{f.file.relative_to(base).as_posix()}:{f.line}  {text}"
        if cite not in seen:
            seen.add(cite)
            out.append(cite)
    return out


def _plain(value):
    """A JSON-safe copy of a fact value; an unresolved expression is shown as such, never as a literal."""
    if isinstance(value, Unresolved):
        return {"expression": value.expr}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _fact_record(repo: Path, f: Fact) -> dict:
    return {"subject": f.subject, "attr": f.attr, "value": _plain(f.value),
            "file": f.file.relative_to(repo.parent).as_posix(), "line": f.line}


def _result(repo: Path, verdict: Verdict) -> dict:
    result: dict = {"status": verdict.status}
    if verdict.status != "missing" and verdict.facts:
        hits = [(f.file, f.line, None) for f in verdict.facts]
        result["evidence"] = _cite(repo, verdict.facts)
        links = _permalinks(repo, hits)
        if links is not None:
            seen = {}
            for f, link in zip(verdict.facts, links):
                seen.setdefault((f.file, f.line), link)
            result["permalinks"] = list(seen.values())
        result["facts"] = [_fact_record(repo, f) for f in verdict.facts]
    if verdict.reason:
        result["reason"] = verdict.reason
    return result


def check(control: str, repo: Path = DEFAULT_REPO) -> dict:
    if control in RULES:
        return _result(repo, RULES[control].run(Product(repo)))
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
    return {**{k: c.desc for k, c in RULES.items()}, **NOT_IN_CODE}


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
