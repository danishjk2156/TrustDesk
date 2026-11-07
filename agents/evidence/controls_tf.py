"""Terraform controls: each reads parsed resources, so comments, look-alike keys and settings on the
wrong resource cannot count as proof. A requirement on "all" resources of a type fails if any one
resource fails; a value that is a variable or expression is `unverifiable`, never a pass.
"""
from __future__ import annotations

from typing import Callable

from loaders import Block, Resource, Terraform, Unresolved, refs
from product import Product
from verdict import Control, Fact, Item, Verdict, absent, failed, judge, passed, unresolved

Predicate = Callable[[object], bool]
Req = tuple[tuple[str, ...], Predicate, str]  # (path to attribute, accepted?, what is required)
Where = Callable[[Resource], "bool | None"]  # True: in scope, False: skip, None: cannot tell


# --- predicates -------------------------------------------------------------------------------

def is_true(v) -> bool:
    return v is True


def is_false(v) -> bool:
    return v is False


def at_least(n: int) -> Predicate:
    return lambda v: type(v) is int and v >= n


def one_of(*allowed: str) -> Predicate:
    return lambda v: isinstance(v, str) and v in allowed


def starts_with(*prefixes: str) -> Predicate:
    return lambda v: isinstance(v, str) and v.startswith(prefixes)


# --- reading one attribute ----------------------------------------------------------------------

def occurrences(block: Block, path: tuple[str, ...]) -> list[tuple[object, int]]:
    """Every (value, line) of the attribute at `path`, across all nested blocks along the way."""
    *blocks, attr = path
    stack = [block]
    for name in blocks:
        stack = [b for cur in stack for b in cur.blocks.get(name, [])]
    return [(b.attrs[attr], b.lines[attr]) for b in stack if attr in b.attrs]


def check_attr(res: Resource, req: Req) -> Item:
    path, ok, wanted = req
    attr = ".".join(path)
    if res.opaque:
        return unresolved(res.address, f"{res.address} uses count, for_each or dynamic blocks")
    found = occurrences(res.body, path)
    if not found:
        return absent(res.address, f"{res.address} does not set {attr}")
    fact = None
    for value, line in found:
        fact = Fact(res.file, line, res.address, attr, value)
        if isinstance(value, Unresolved):
            return unresolved(res.address, f"{res.address}.{attr} is an expression, not a literal", fact)
        if not ok(value):
            return failed(fact, f"{res.address}.{attr} is {value!r}; required: {wanted}")
    return passed(fact)


def attr_equals(path: tuple[str, ...], *allowed: str) -> Where:
    """Scope a check to resources whose attribute is one of the given values."""
    def where(res: Resource):
        values = occurrences(res.body, path)
        if not values:
            return False
        if any(isinstance(v, Unresolved) for v, _ in values):
            return None
        return any(v in allowed for v, _ in values)
    return where


def resources_check(types: tuple[str, ...], reqs: list[Req], nothing: str, where: Where | None = None) -> Callable[[Product], Verdict]:
    def run(p: Product) -> Verdict:
        tf = p.terraform
        items: list[Item] = []
        for res in tf.of_type(*types):
            scope = where(res) if where else True
            if scope is False:
                continue
            if scope is None:
                items.append(unresolved(res.address, f"{res.address}: cannot tell whether this check applies"))
                continue
            items += [check_attr(res, req) for req in reqs]
        return judge(items, nothing, tf.complete)
    return run


def single(types: tuple[str, ...], attr: str, ok: Predicate, wanted: str, nothing: str) -> Callable[[Product], Verdict]:
    return resources_check(types, [((attr,), ok, wanted)], nothing)


def linked(tf: Terraform, rtype: str, target: Resource, attr: str = "bucket") -> list[Resource]:
    """Resources of `rtype` whose `attr` points at `target`, by reference or by identical literal name."""
    out = []
    for res in tf.of_type(rtype):
        value = res.body.attrs.get(attr)
        literal = target.body.attrs.get(attr)
        if target.address in refs(value) or (isinstance(value, str) and isinstance(literal, str) and value == literal):
            out.append(res)
    return out


# --- per-bucket controls ------------------------------------------------------------------------

BUCKET_TYPES = ("aws_s3_bucket",)
SSE_ALGORITHMS = ("aws:kms", "aws:kms:dsse", "AES256")


def per_bucket(tf: Terraform, rtype: str, reqs: list[Req], what: str, absent_fails: bool = False) -> list[Item]:
    items: list[Item] = []
    for bucket in tf.of_type(*BUCKET_TYPES):
        if bucket.opaque:
            items.append(unresolved(bucket.address, f"{bucket.address} uses count or for_each"))
            continue
        others = linked(tf, rtype, bucket)
        if not others:
            reason = f"no {what} for bucket {bucket.name}"
            fact = Fact(bucket.file, bucket.line, bucket.address, what, None)
            items.append(failed(fact, reason) if absent_fails else absent(bucket.address, reason))
            continue
        items += [check_attr(o, req) for o in others for req in reqs]
    return items


def storage_versioning(p: Product) -> Verdict:
    tf = p.terraform
    reqs: list[Req] = [(("versioning_configuration", "status"), one_of("Enabled"), "Enabled")]
    return judge(per_bucket(tf, "aws_s3_bucket_versioning", reqs, "versioning configuration"),
                 "No S3 buckets defined in terraform/", tf.complete)


PUBLIC_BLOCKS = ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")


def storage_public_access_blocked(p: Product) -> Verdict:
    tf = p.terraform
    reqs: list[Req] = [((flag,), is_true, "true") for flag in PUBLIC_BLOCKS]
    return judge(per_bucket(tf, "aws_s3_bucket_public_access_block", reqs, "public access block"),
                 "No S3 buckets defined in terraform/", tf.complete)


def storage_encryption_all_buckets(p: Product) -> Verdict:
    tf = p.terraform
    reqs: list[Req] = [(("rule", "apply_server_side_encryption_by_default", "sse_algorithm"), one_of(*SSE_ALGORITHMS),
                        "a server-side encryption algorithm")]
    items = per_bucket(tf, "aws_s3_bucket_server_side_encryption_configuration", reqs, "encryption configuration", True)
    unencrypted = [i.subject.split(".", 1)[1] for i in items if i.fact and i.fact.attr == "encryption configuration"]
    verdict = judge(items, "No S3 buckets defined in terraform/", tf.complete)
    if unencrypted and verdict.status == "contradicted":
        total = len(tf.of_type(*BUCKET_TYPES))
        reason = f"{len(unencrypted)} of {total} S3 buckets have no encryption configuration: " + ", ".join(unencrypted)
        return Verdict("contradicted", verdict.facts, reason)
    return verdict


# --- cross-resource controls --------------------------------------------------------------------

def db_tls_required(p: Product) -> Verdict:
    tf = p.terraform
    items: list[Item] = []
    for db in tf.of_type("aws_db_instance"):
        group_ref = db.body.attrs.get("parameter_group_name")
        line = db.body.lines.get("parameter_group_name", db.line)
        if group_ref is None:
            items.append(absent(db.address, f"{db.address} has no parameter group, so rds.force_ssl is not set"))
            continue
        fact = Fact(db.file, line, db.address, "parameter_group_name", group_ref)
        groups = [g for g in tf.of_type("aws_db_parameter_group") if g.address in refs(group_ref)]
        if not groups:
            items.append(unresolved(db.address, f"{db.address} parameter group is not defined in this repo", fact))
            continue
        for group in groups:
            for param in group.body.blocks.get("parameter", []):
                if param.attrs.get("name") != "rds.force_ssl":
                    continue
                value = param.attrs.get("value")
                pf = Fact(group.file, param.lines.get("value", group.line), group.address, "parameter rds.force_ssl", value)
                if isinstance(value, Unresolved):
                    items.append(unresolved(db.address, f"{group.address} rds.force_ssl is an expression", pf))
                elif value in ("1", 1, True):
                    items.append(passed(pf))
                else:
                    items.append(failed(pf, f"{group.address} sets rds.force_ssl to {value!r}"))
                break
            else:
                items.append(absent(db.address, f"{group.address} does not set rds.force_ssl"))
    return judge(items, "No aws_db_instance defined in terraform/", tf.complete)


def waf(p: Product) -> Verdict:
    tf = p.terraform
    items: list[Item] = []
    for lb in tf.of_type("aws_lb", "aws_alb"):
        internal = lb.body.attrs.get("internal", False)
        if internal is True:
            continue
        if isinstance(internal, Unresolved):
            items.append(unresolved(lb.address, f"{lb.address} internal is an expression"))
            continue
        assoc = [a for a in tf.of_type("aws_wafv2_web_acl_association") if lb.address in refs(a.body.attrs.get("resource_arn"))]
        if not assoc:
            items.append(absent(lb.address, f"public load balancer {lb.address} has no WAF association"))
            continue
        a = assoc[0]
        items.append(passed(Fact(a.file, a.body.lines["resource_arn"], a.address, "resource_arn", a.body.attrs["resource_arn"])))
    return judge(items, "No public load balancer defined in terraform/", tf.complete)


def network_flow_logs(p: Product) -> Verdict:
    tf = p.terraform
    items: list[Item] = []
    for vpc in tf.of_type("aws_vpc"):
        logs = [f for f in tf.of_type("aws_flow_log") if vpc.address in refs(f.body.attrs.get("vpc_id"))]
        if not logs:
            items.append(absent(vpc.address, f"{vpc.address} has no flow log"))
            continue
        items += [check_attr(f, (("traffic_type",), one_of("ALL"), "ALL")) for f in logs]
    return judge(items, "No aws_vpc defined in terraform/", tf.complete)


def db_network_restricted(p: Product) -> Verdict:
    tf = p.terraform
    items: list[Item] = []
    for db in tf.of_type("aws_db_instance"):
        sgs = [s for s in tf.of_type("aws_security_group") if s.address in refs(db.body.attrs.get("vpc_security_group_ids"))]
        if not sgs:
            items.append(absent(db.address, f"{db.address} has no security group defined in this repo"))
            continue
        for sg in sgs:
            extra = [r for r in tf.of_type("aws_security_group_rule", "aws_vpc_security_group_ingress_rule")
                     if sg.address in refs(r.body.attrs.get("security_group_id"))]
            if extra:
                items.append(unresolved(sg.address, f"{sg.address} also has separate ingress rule resources"))
                continue
            rules = sg.body.blocks.get("ingress", [])
            if not rules:
                items.append(absent(sg.address, f"{sg.address} has no ingress rule restricting access"))
            for rule in rules:
                open_to = [k for k in ("cidr_blocks", "ipv6_cidr_blocks", "self", "prefix_list_ids") if k in rule.attrs]
                if open_to or "security_groups" not in rule.attrs:
                    items.append(failed(Fact(sg.file, rule.line, sg.address, "ingress", open_to),
                                        f"{sg.address} ingress is not limited to security groups"))
                else:
                    items.append(passed(Fact(sg.file, rule.lines["security_groups"], sg.address, "ingress.security_groups",
                                             rule.attrs["security_groups"])))
    return judge(items, "No aws_db_instance defined in terraform/", tf.complete)


def least_privilege(p: Product) -> Verdict:
    tf = p.terraform
    attachments = tf.of_type("aws_iam_role_policy_attachment")
    read_only = [a for a in attachments if isinstance(a.body.attrs.get("policy_arn"), str)
                 and a.body.attrs["policy_arn"].endswith("policy/ReadOnlyAccess")]
    items: list[Item] = []
    for att in read_only:
        role = att.body.attrs.get("role")
        fact = Fact(att.file, att.body.lines["policy_arn"], att.address, "policy_arn", att.body.attrs["policy_arn"])
        targets = [r for r in tf.of_type("aws_iam_role") if r.address in refs(role)]
        if not targets:
            items.append(unresolved(att.address, f"{att.address} role is not defined in this repo", fact))
            continue
        extra = [o for o in attachments if o is not att and refs(o.body.attrs.get("role")) & refs(role)]
        broader = [o for o in extra if o.body.attrs.get("policy_arn") != att.body.attrs["policy_arn"]]
        if broader:
            items.append(failed(fact, f"role also has {broader[0].address}, so it is not read-only"))
        else:
            items.append(passed(fact))
    return judge(items, "No ReadOnlyAccess role attachment defined in terraform/", tf.complete)


def mfa_policy_attachments(tf: Terraform, policy: Resource) -> list[Resource]:
    kinds = ("aws_iam_policy_attachment", "aws_iam_role_policy_attachment",
             "aws_iam_group_policy_attachment", "aws_iam_user_policy_attachment")
    return [a for a in tf.of_type(*kinds) if policy.address in refs(a.body.attrs.get("policy_arn"))]


CONTROLS: dict[str, Control] = {
    "db_encryption_at_rest": Control("Database storage encrypted with KMS",
        single(("aws_db_instance", "aws_rds_cluster"), "storage_encrypted", is_true, "true", "No database defined in terraform/")),
    "kms_key_rotation": Control("KMS keys rotate automatically",
        single(("aws_kms_key",), "enable_key_rotation", is_true, "true", "No KMS key defined in terraform/")),
    "tls_in_transit": Control("Public endpoints use TLS 1.2+", resources_check(
        ("aws_lb_listener", "aws_alb_listener"),
        [(("ssl_policy",), starts_with("ELBSecurityPolicy-TLS13-1-2", "ELBSecurityPolicy-TLS13-1-3", "ELBSecurityPolicy-TLS-1-2"),
          "a TLS 1.2+ policy")],
        "No HTTPS listener defined in terraform/", attr_equals(("protocol",), "HTTPS", "TLS"))),
    "https_redirect": Control("Plain HTTP redirects to HTTPS", resources_check(
        ("aws_lb_listener", "aws_alb_listener"),
        [(("default_action", "type"), one_of("redirect"), "redirect"),
         (("default_action", "redirect", "protocol"), one_of("HTTPS"), "HTTPS"),
         (("default_action", "redirect", "status_code"), one_of("HTTP_301", "HTTP_302"), "HTTP_301 or HTTP_302")],
        "No plain-HTTP listener defined in terraform/", attr_equals(("protocol",), "HTTP"))),
    "db_tls_required": Control("Database requires TLS connections", db_tls_required),
    "password_length": Control("Minimum password length 14",
        single(("aws_iam_account_password_policy",), "minimum_password_length", at_least(14), ">= 14", "No account password policy defined")),
    "password_reuse": Control("Password reuse prevented",
        single(("aws_iam_account_password_policy",), "password_reuse_prevention", at_least(1), ">= 1", "No account password policy defined")),
    "least_privilege": Control("Read-only role for auditors", least_privilege),
    "audit_logging": Control("Multi-region audit trail",
        single(("aws_cloudtrail",), "is_multi_region_trail", is_true, "true", "No CloudTrail defined in terraform/")),
    "log_integrity": Control("Audit log file validation",
        single(("aws_cloudtrail",), "enable_log_file_validation", is_true, "true", "No CloudTrail defined in terraform/")),
    "log_retention": Control("Logs kept for a year",
        single(("aws_cloudwatch_log_group",), "retention_in_days", at_least(365), ">= 365 days", "No log group defined in terraform/")),
    "network_flow_logs": Control("VPC flow logs", network_flow_logs),
    "db_backups": Control("Automated database backups",
        single(("aws_db_instance",), "backup_retention_period", at_least(1), ">= 1 day", "No aws_db_instance defined in terraform/")),
    "multi_az": Control("Database runs multi-AZ",
        single(("aws_db_instance",), "multi_az", is_true, "true", "No aws_db_instance defined in terraform/")),
    "deletion_protection": Control("Database deletion protection",
        single(("aws_db_instance",), "deletion_protection", is_true, "true", "No aws_db_instance defined in terraform/")),
    "storage_versioning": Control("Object versioning on storage", storage_versioning),
    "storage_public_access_blocked": Control("Public access blocked on storage", storage_public_access_blocked),
    "waf": Control("Web application firewall on the public load balancer", waf),
    "db_not_public": Control("Database not reachable from the internet",
        single(("aws_db_instance",), "publicly_accessible", is_false, "false", "No aws_db_instance defined in terraform/")),
    "db_network_restricted": Control("Database accepts only the app tier", db_network_restricted),
    "storage_encryption_all_buckets": Control("Every S3 bucket has server-side encryption", storage_encryption_all_buckets),
}
