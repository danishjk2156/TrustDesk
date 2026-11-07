"""YAML, JSON, CI-workflow and text controls, read as parsed data (comments never count).

A file that fails to parse, or has a duplicated key, makes the check `unverifiable`: the verifier
will not guess which of two conflicting values is the real one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from controls_tf import Req, at_least, is_true, mfa_policy_attachments, one_of
from loaders import Document, Unresolved, strip_markdown_comments
from product import Product
from verdict import Control, Fact, Item, Verdict, absent, failed, judge, passed, unresolved


def _name(p: Product, path: Path) -> str:
    return path.relative_to(p.root.parent).as_posix()


def _fact(p: Product, doc: Document, path: tuple, value) -> Fact | None:
    line = doc.line(*path)
    return Fact(doc.path, line, _name(p, doc.path), ".".join(str(s) for s in path), value) if line else None


def _nonempty_string(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def check_doc(p: Product, doc: Document, req: Req, prefix: tuple = ()) -> Item | None:
    """None when the key is absent; otherwise pass/fail/unresolved for its value."""
    path, ok, wanted = req
    full = prefix + path
    value = doc.get(*full)
    if value is None:
        return None
    fact = _fact(p, doc, full, value)
    subject = _name(p, doc.path)
    if fact is None:
        return unresolved(subject, f"{subject} {'.'.join(map(str, full))} has no single line (alias or merge key)")
    if not ok(value):
        return failed(fact, f"{subject} {'.'.join(map(str, full))} is {value!r}; required: {wanted}")
    return passed(fact)


def yaml_control(globs: tuple[str, ...], reqs: list[Req], nothing: str) -> Callable[[Product], Verdict]:
    """Every file defining the first key must satisfy every requirement."""
    def run(p: Product) -> Verdict:
        items: list[Item] = []
        for doc in p.documents(*globs):
            if doc.error:
                items.append(unresolved(_name(p, doc.path), f"{_name(p, doc.path)} cannot be read: {doc.error}"))
                continue
            if doc.get(*reqs[0][0][:1]) is None:
                continue
            for req in reqs:
                item = check_doc(p, doc, req)
                items.append(item or absent(_name(p, doc.path), f"{_name(p, doc.path)} does not set {'.'.join(req[0])}"))
        return judge(items, nothing)
    return run


# --- CI workflows -------------------------------------------------------------------------------

WORKFLOWS = (".github/workflows/*.yml", ".github/workflows/*.yaml")


def _triggers(doc: Document) -> set[str]:
    on = doc.get("on") if doc.get("on") is not None else doc.get(True)  # YAML reads a bare `on:` as True
    if isinstance(on, str):
        return {on}
    return {str(k) for k in on} if isinstance(on, (list, dict)) else set()


def _is_off(value) -> bool:
    return value is False or (isinstance(value, str) and value.strip().strip("${} ").lower() == "false")


def workflow_uses(action: str) -> Callable[[Product], Verdict]:
    """An enabled step that runs `action@version` in a job that runs on push or pull request."""
    def run(p: Product) -> Verdict:
        items: list[Item] = []
        for doc in p.documents(*WORKFLOWS):
            name = _name(p, doc.path)
            if doc.error:
                items.append(unresolved(name, f"{name} cannot be read: {doc.error}"))
                continue
            if not _triggers(doc) & {"push", "pull_request"}:
                continue
            for job, body in (doc.get("jobs") or {}).items():
                if not isinstance(body, dict) or _is_off(body.get("if")):
                    continue
                for i, step in enumerate(body.get("steps") or []):
                    uses = step.get("uses") if isinstance(step, dict) else None
                    if not (isinstance(uses, str) and re.match(rf"^{re.escape(action)}@\S+$", uses)):
                        continue
                    if _is_off(step.get("if")) or step.get("continue-on-error") in (True, "true"):
                        continue
                    fact = _fact(p, doc, ("jobs", job, "steps", i, "uses"), uses)
                    items.append(passed(fact) if fact else unresolved(name, f"{name} step has no single line"))
        return judge(items, f"No enabled CI step runs {action}")
    return run


# --- specific controls --------------------------------------------------------------------------

def code_review_required(p: Product) -> Verdict:
    items: list[Item] = []
    for doc in p.documents(".github/settings.yml", ".github/settings.yaml"):
        name = _name(p, doc.path)
        if doc.error:
            items.append(unresolved(name, f"{name} cannot be read: {doc.error}"))
            continue
        for i, branch in enumerate(doc.get("branches") or []):
            if isinstance(branch, dict) and branch.get("name") == "main":
                req: Req = (("protection", "required_pull_request_reviews", "required_approving_review_count"),
                            at_least(1), ">= 1")
                item = check_doc(p, doc, req, ("branches", i))
                items.append(item or absent(name, f"{name}: main has no required review count"))
    return judge(items, "No branch protection for main in .github/settings.yml")


def dependency_scanning(p: Product) -> Verdict:
    items: list[Item] = []
    for doc in p.documents(".github/dependabot.yml", ".github/dependabot.yaml"):
        name = _name(p, doc.path)
        if doc.error:
            items.append(unresolved(name, f"{name} cannot be read: {doc.error}"))
            continue
        if doc.get("version") != 2:
            items.append(failed(_fact(p, doc, ("version",), doc.get("version")) or Fact(doc.path, 1, name, "version", None),
                                f"{name} is not a version 2 dependabot config"))
            continue
        for i, update in enumerate(doc.get("updates") or []):
            if isinstance(update, dict) and _nonempty_string(update.get("package-ecosystem")) \
                    and isinstance(update.get("schedule"), dict) and update["schedule"].get("interval"):
                fact = _fact(p, doc, ("updates", i, "package-ecosystem"), update["package-ecosystem"])
                if fact:
                    items.append(passed(fact))
    return judge(items, "No scheduled dependency update defined in dependabot.yml")


def vulnerability_disclosure(p: Product) -> Verdict:
    lines = p.text("SECURITY.md")
    path = p.root / "SECURITY.md"
    if lines is None:
        return Verdict("missing", (), "No SECURITY.md")
    visible = strip_markdown_comments("\n".join(lines)).splitlines()
    for n, line in enumerate(visible, start=1):
        m = re.search(r"\bsecurity@[\w-]+(?:\.[\w-]+)+", line)
        if m:
            return Verdict("found", (Fact(path, n, "SECURITY.md", "contact", m.group(0)),))
    return Verdict("missing", (), "No security@ contact in SECURITY.md")


def _strip_nginx_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def hsts(p: Product) -> Verdict:
    items: list[Item] = []
    for path in sorted(p.root.glob("configs/*.conf")):
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = re.match(r"\s*add_header\s+Strict-Transport-Security\s+\"?max-age=(\d+)", _strip_nginx_comment(raw))
            if not m:
                continue
            fact = Fact(path, n, _name(p, path), "Strict-Transport-Security", int(m.group(1)))
            items.append(passed(fact) if int(m.group(1)) >= 15552000 else failed(fact, "HSTS max-age is under 180 days"))
    return judge(items, "No Strict-Transport-Security header in configs/")


def _file_call(expr) -> str | None:
    """The relative path in `file("${path.module}/rel")`, or None for anything else."""
    if isinstance(expr, Unresolved):
        m = re.fullmatch(r'\$\{file\("\$\{path\.module\}/([^"$]+)"\)\}', expr.expr)
        return m.group(1) if m else None
    return None


def _mfa_condition(doc: Document) -> tuple | None:
    """Path to the aws:MultiFactorAuthPresent condition of a Deny-everything-without-MFA statement."""
    statements = doc.get("Statement")
    single = isinstance(statements, dict)
    for i, stmt in enumerate([statements] if single else statements or []):
        if not isinstance(stmt, dict) or stmt.get("Effect") != "Deny" or "Principal" in stmt:
            continue
        if stmt.get("Resource") not in ("*", ["*"]):
            continue
        cond = (stmt.get("Condition") or {}).get("BoolIfExists")  # Bool alone passes when the key is absent
        if isinstance(cond, dict) and cond.get("aws:MultiFactorAuthPresent") in ("false", False):
            head = ("Statement",) if single else ("Statement", i)
            return head + ("Condition", "BoolIfExists", "aws:MultiFactorAuthPresent")
    return None


def mfa_admin(p: Product) -> Verdict:
    tf = p.terraform
    items: list[Item] = []
    for policy in tf.of_type("aws_iam_policy"):
        rel = _file_call(policy.body.attrs.get("policy"))
        if rel is None:
            continue
        target = (p.root / "terraform" / rel).resolve()
        if p.root.resolve() not in target.parents:
            continue
        doc = p.documents(target.relative_to(p.root.resolve()).as_posix())
        if not doc:
            continue
        doc = doc[0]
        if doc.error:
            items.append(unresolved(policy.address, f"{doc.path.name} cannot be read: {doc.error}"))
            continue
        cond = _mfa_condition(doc)
        fact = _fact(p, doc, cond, "false") if cond else None
        if not fact:
            continue  # not an MFA policy
        attached = mfa_policy_attachments(tf, policy)
        if not attached:
            items.append(absent(policy.address, f"{policy.address} denies without MFA but is attached to no role, group or user in this repo"))
            continue
        a = attached[0]
        items += [passed(fact), passed(Fact(a.file, a.body.lines["policy_arn"], a.address, "policy_arn", a.body.attrs["policy_arn"]))]
    return judge(items, "No IAM policy denying actions without MFA", tf.complete)


CONTROLS: dict[str, Control] = {
    "hsts": Control("HSTS header set", hsts),
    "mfa_admin": Control("MFA required for admin actions", mfa_admin),
    "mfa_all_users": Control("MFA enforced for every workforce user", yaml_control(
        ("configs/*.yaml", "configs/*.yml"), [(("mfa", "enforced_for_all_users"), is_true, "true")],
        "No mfa.enforced_for_all_users setting in configs/")),
    "sso": Control("Workforce SSO", yaml_control(
        ("configs/*.yaml", "configs/*.yml"),
        [(("sso", "provider"), _nonempty_string, "an identity provider"), (("sso", "protocol"), one_of("saml", "oidc"), "saml or oidc")],
        "No sso settings in configs/")),
    "session_timeout": Control("Session timeout configured", yaml_control(
        ("configs/*.yaml", "configs/*.yml"), [(("session_timeout_minutes",), at_least(1), ">= 1 minute")],
        "No session_timeout_minutes in configs/")),
    "code_review_required": Control("Reviewed pull requests required on main", code_review_required),
    "sast": Control("Static analysis in CI", workflow_uses("github/codeql-action/analyze")),
    "dependency_scanning": Control("Dependency updates and alerts", dependency_scanning),
    "secrets_scanning": Control("Secret scanning in CI", workflow_uses("gitleaks/gitleaks-action")),
    "container_scanning": Control("Container image scanning in CI", workflow_uses("aquasecurity/trivy-action")),
    "vulnerability_disclosure": Control("Public vulnerability reporting channel", vulnerability_disclosure),
}
