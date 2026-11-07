"""TrustDesk remediator: turn a gap the evidence engine found into a code fix, and prove the fix.

Applies a known recipe for a control to the product repo's working tree, then re-runs the
evidence engine on the result. It changes files only; the remediator agent does the git branch,
commit, push and pull request.

    python tools/remediate.py --list
    python tools/remediate.py storage_encryption_all_buckets
    python tools/remediate.py mfa_all_users --dry-run

Prints JSON: the recipe, files changed, validation, and the evidence before and after.
The fix is validated independently (tools/remediation_check.py) and rolled back if it fails.
`--require-tools` makes a missing terraform or checkov a failure instead of a skipped check.
Exit 0 = fixed, validated and now proven, 1 = applied but still not proven (rolled back),
4 = failed independent validation (rolled back), 3 = no recipe for this control (or nothing to
fix), 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import remediation_check  # noqa: E402
import search  # noqa: E402

SSE_BLOCK = '''
resource "aws_s3_bucket_server_side_encryption_configuration" "{name}" {{
  bucket = aws_s3_bucket.{name}.id

  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }}
  }}
}}
'''


def _fix_bucket_encryption(repo: Path, dry: bool) -> list[str]:
    result = search.check("storage_encryption_all_buckets", repo)
    if result["status"] != "contradicted":
        return []
    names = [f["subject"].split(".", 1)[1] for f in result["facts"] if f["subject"].startswith("aws_s3_bucket.")]
    if not names:
        return []
    s3 = repo / "terraform" / "s3.tf"
    if not dry:
        with s3.open("a", encoding="utf-8") as f:
            for name in names:
                f.write(SSE_BLOCK.format(name=name))
    return [f"{s3.relative_to(repo.parent).as_posix()}: add KMS encryption to bucket(s) {', '.join(names)}"]


def _fix_mfa_all_users(repo: Path, dry: bool) -> list[str]:
    auth = repo / "configs" / "auth.yaml"
    text = auth.read_text(encoding="utf-8")
    if "enforced_for_all_users: false" not in text:
        return []
    if not dry:
        auth.write_text(text.replace("enforced_for_all_users: false", "enforced_for_all_users: true"), encoding="utf-8")
    return [f"{auth.relative_to(repo.parent).as_posix()}: enforce MFA for all workforce users"]


Scope = remediation_check.Scope
RECIPES = {
    "storage_encryption_all_buckets": (
        "Encrypt every S3 bucket with the customer-data KMS key", _fix_bucket_encryption,
        Scope({"terraform/s3.tf": "append"}, frozenset({"aws_s3_bucket_server_side_encryption_configuration"}))),
    "mfa_all_users": (
        "Require MFA for every workforce user, not only admins", _fix_mfa_all_users,
        Scope({"configs/auth.yaml": {"mfa.enforced_for_all_users"}})),
}


def remediate(control: str, repo: Path = search.DEFAULT_REPO, dry: bool = False, require_tools: bool = False) -> tuple[dict, int]:
    if control not in RECIPES:
        return {"control": control, "status": "no-recipe",
                "reason": "This gap needs a person (a document, a test, a contract), not a code change.",
                "recipes": sorted(RECIPES)}, 3
    title, fix, scope = RECIPES[control]
    before = search.check(control, repo)
    base = remediation_check.baseline(repo)
    changes = fix(repo, dry)
    if not changes:
        return {"control": control, "status": "nothing-to-fix", "before": before}, 3
    if dry:
        return {"control": control, "recipe": title, "status": "dry-run", "changes": changes, "before": before, "after": before}, 0
    problems, skipped = remediation_check.validate(repo, base, scope, require_tools=require_tools)
    after = search.check(control, repo)
    if after["status"] != "found":
        problems.append(f"the evidence engine still says {after['status']} for {control}")
    report = {"control": control, "recipe": title, "changes": changes, "before": before, "after": after,
              "validation": {"problems": problems, "skipped_tools": skipped}}
    if problems:
        remediation_check.restore(repo, base)
        code = 4 if any(not p.startswith("the evidence engine") for p in problems) else 1
        return {**report, "status": "rejected" if code == 4 else "not-proven", "rolled_back": True}, code
    return {**report, "status": "proven"}, 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("control", nargs="?")
    ap.add_argument("--repo", type=Path, default=search.DEFAULT_REPO)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--require-tools", action="store_true", help="fail if terraform or checkov is not installed")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.list or not args.control:
        for k, (title, *_rest) in RECIPES.items():
            print(f"{k:32} {title}")
        return 0 if args.list else 2
    report, code = remediate(args.control, args.repo.resolve(), args.dry_run, args.require_tools)
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
