"""Independent validation of a remediation before anyone opens a PR for it.

A recipe is trusted to *try* a fix, not to be right. After it runs, this module checks the result
without asking the recipe:

  1. only the files the recipe declared changed, nothing was deleted, and a Terraform file only grew
     (every existing byte is unchanged); a YAML file changed only the declared keys (dotted paths, e.g. mfa.enforced_for_all_users)
  2. the Terraform still parses, has no duplicate resource, lost no resource, added only declared
     resource types, and every reference in a new resource points at a resource that exists
  3. changed YAML/JSON still parses with no duplicate keys
  4. no control the evidence engine proved before is any weaker now
  5. when installed, `terraform fmt -check` and `checkov` (no NEW failed check); with `require_tools`
     a missing tool is a failure, never a pass

Any problem means the caller restores the files. Nothing here merges or pushes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import loaders  # noqa: E402
import search  # noqa: E402

SKIP_DIRS = {".git", ".terraform"}
NOT_RESOURCES = {"var", "local", "data", "module", "each", "count", "self", "path", "terraform"}
TOOL_TIMEOUT = 120


@dataclass(frozen=True)
class Scope:
    """What a recipe may touch. `files` maps a path (relative to the repo) to "append" (Terraform: add
    only) or the set of YAML keys it may change; `new_types` are the resource types it may add."""
    files: dict
    new_types: frozenset = frozenset()


@dataclass
class Baseline:
    files: dict[str, bytes]
    addresses: set[str]
    statuses: dict[str, str]
    checkov: set[str] = field(default_factory=set)


def _read_all(repo: Path) -> dict[str, bytes]:
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in sorted(repo.rglob("*"))
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(repo).parts)}


def run_tool(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run an external tool. A module function so tests can stand in for the real binaries."""
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    return done.returncode, done.stdout + done.stderr


def _checkov_failures(repo: Path) -> set[str]:
    code, out = run_tool(["checkov", "-d", str(repo / "terraform"), "-o", "json", "--quiet"], repo)
    try:
        report = json.loads(out)
    except ValueError:
        raise RuntimeError("checkov output was not JSON") from None
    reports = report if isinstance(report, list) else [report]
    return {f"{f['check_id']}:{f['resource']}" for r in reports for f in r.get("results", {}).get("failed_checks", [])}


def baseline(repo: Path, *, tools: bool = True) -> Baseline:
    tf = loaders.load_terraform(repo / "terraform")
    base = Baseline(_read_all(repo), {r.address for r in tf.resources},
                    {c: search.check(c, repo)["status"] for c in search.RULES})
    if tools and shutil.which("checkov"):
        base.checkov = _checkov_failures(repo)
    return base


def restore(repo: Path, base: Baseline) -> None:
    for path in _read_all(repo):
        if path not in base.files:
            (repo / path).unlink()
    for path, data in base.files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(data)


def _leaves(data, prefix="") -> dict:
    if isinstance(data, dict):
        return {k: v for key, val in data.items() for k, v in _leaves(val, f"{prefix}{key}.").items()}
    if isinstance(data, list):
        return {k: v for i, val in enumerate(data) for k, v in _leaves(val, f"{prefix}{i}.").items()}
    return {prefix.rstrip("."): data}


def _parse_bytes(path: Path, data: bytes) -> loaders.Document:
    """Parse `data` as if it were the file at `path`; the file on disk is left as it is now."""
    live = path.read_bytes()
    try:
        path.write_bytes(data)
        return loaders.load_document(path)
    finally:
        path.write_bytes(live)


def _file_problems(repo: Path, base: Baseline, scope: Scope) -> list[str]:
    now = _read_all(repo)
    out = []
    for path in sorted(set(base.files) | set(now)):
        if base.files.get(path) == now.get(path):
            continue
        rule = scope.files.get(path)
        if path not in now:
            out.append(f"{path} was deleted")
        elif rule is None:
            out.append(f"{path} changed but the recipe does not declare it")
        elif path not in base.files:
            out.append(f"{path} is a new file the recipe does not declare")
        elif rule == "append":
            if not now[path].startswith(base.files[path]):
                out.append(f"{path}: existing Terraform was modified, not only appended to")
        else:
            old = _parse_bytes(repo / path, base.files[path])
            new = _parse_bytes(repo / path, now[path])
            if old.error or new.error:
                out.append(f"{path}: does not parse ({new.error or old.error})")
                continue
            before, after = _leaves(old.data), _leaves(new.data)
            wrong = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k) and k not in rule)
            if wrong:
                out.append(f"{path}: changed keys the recipe may not touch: {', '.join(wrong)}")
    return out


def _terraform_problems(repo: Path, base: Baseline, scope: Scope) -> list[str]:
    tf = loaders.load_terraform(repo / "terraform")
    out = [f"Terraform does not parse: {e}" for e in tf.errors]
    addresses = [r.address for r in tf.resources]
    out += [f"duplicate resource {a}" for a in sorted({a for a in addresses if addresses.count(a) > 1})]
    out += [f"resource {a} was removed" for a in sorted(base.addresses - set(addresses))]
    for r in tf.resources:
        if r.address in base.addresses:
            continue
        if r.rtype not in scope.new_types:
            out.append(f"new resource {r.address} has a type the recipe may not add")
        for ref in sorted(_block_refs(r.body)):
            if ref.split(".")[0] not in NOT_RESOURCES and ref not in addresses:
                out.append(f"{r.address} refers to {ref}, which does not exist")
    return out


def _block_refs(block) -> set[str]:
    found = set().union(*[loaders.refs(v) for v in block.attrs.values()]) if block.attrs else set()
    for children in block.blocks.values():
        for child in children:
            found |= _block_refs(child)
    return found


def _regressions(repo: Path, base: Baseline) -> list[str]:
    return [f"control {c} was {base.statuses[c]} and is now {now}" for c in search.RULES
            if base.statuses[c] == "found" and (now := search.check(c, repo)["status"]) != "found"]


def _tool_problems(repo: Path, base: Baseline, require_tools: bool) -> tuple[list[str], list[str]]:
    problems, skipped = [], []
    if shutil.which("terraform"):
        code, out = run_tool(["terraform", "fmt", "-check", "-diff", "-no-color"], repo / "terraform")
        if code != 0:
            problems.append(f"terraform fmt -check failed: {out.strip()[:200]}")
        if (repo / "terraform" / ".terraform").is_dir():
            code, out = run_tool(["terraform", "validate", "-no-color"], repo / "terraform")
            if code != 0:
                problems.append(f"terraform validate failed: {out.strip()[:200]}")
    else:
        skipped.append("terraform")
    if shutil.which("checkov"):
        try:
            problems += [f"checkov: new failed check {f}" for f in sorted(_checkov_failures(repo) - base.checkov)]
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            problems.append(f"checkov could not run: {exc}")
    else:
        skipped.append("checkov")
    if require_tools and skipped:
        problems.append(f"required validation tool(s) not installed: {', '.join(skipped)}")
    return problems, skipped


def validate(repo: Path, base: Baseline, scope: Scope, *, require_tools: bool = False) -> tuple[list[str], list[str]]:
    """(problems, skipped tools). An empty problem list is the only pass."""
    problems = _file_problems(repo, base, scope)
    if not any("does not parse" in p or "deleted" in p for p in problems):
        problems += _terraform_problems(repo, base, scope) + _regressions(repo, base)
    tool_problems, skipped = _tool_problems(repo, base, require_tools)
    return problems + tool_problems, skipped
