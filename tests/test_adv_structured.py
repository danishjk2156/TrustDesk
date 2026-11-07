"""Release blockers for the structured verifier: look-alike code must never be `found`.

Each case is a tiny product repo that LOOKS like it satisfies a control, with the status the verifier
must return. `NOT_FOUND` means any status except `found`: the one outcome that would be a false-positive
VERIFIED answer. Each "good" twin at the end proves the check can still say `found`.

    python -m unittest discover -s tests -p "test_adv_*.py" -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "evidence"))
import search  # noqa: E402

NOT_FOUND = "not-found"

DB = 'resource "aws_db_instance" "main" {\n%s}\n'
LISTENER = 'resource "aws_lb_listener" "l" {\n  protocol = "%s"\n%s}\n'
MFA_JSON = ('{"Statement": [{"Effect": "%s", "NotAction": ["iam:ChangePassword"], "Resource": "*", '
            '"Condition": {"%s": {"aws:MultiFactorAuthPresent": "false"}}}]}')
MFA_TF = ('resource "aws_iam_policy" "mfa" {\n  policy = file("${path.module}/../iam/mfa.json")\n}\n'
          'resource "aws_iam_group_policy_attachment" "a" {\n  group = "admins"\n  policy_arn = aws_iam_policy.mfa.arn\n}\n')
WORKFLOW = "on: %s\njobs:\n  scan:\n    runs-on: ubuntu-latest\n%s    steps:\n      - %s\n"
CODEQL = "uses: github/codeql-action/analyze@v3"

CASES = [
    # (name, files, control, expected status)
    ("db encrypted only inside a description string",
     {"terraform/d.tf": DB % '  description = "storage_encrypted = true"\n'}, "db_encryption_at_rest", NOT_FOUND),
    ("db encrypted via count", {"terraform/d.tf": DB % "  count = var.n\n  storage_encrypted = true\n"},
     "db_encryption_at_rest", "unverifiable"),
    ("db encrypted via for_each", {"terraform/d.tf": DB % "  for_each = var.dbs\n  storage_encrypted = true\n"},
     "db_encryption_at_rest", "unverifiable"),
    ("a module hides other databases",
     {"terraform/d.tf": DB % "  storage_encrypted = true\n", "terraform/m.tf": 'module "extra" {\n  source = "./extra"\n}\n'},
     "db_encryption_at_rest", "unverifiable"),
    ("a terraform file that does not parse",
     {"terraform/d.tf": DB % "  storage_encrypted = true\n", "terraform/broken.tf": 'resource "x" {{{'},
     "db_encryption_at_rest", "unverifiable"),
    ("no database at all", {"terraform/d.tf": 'resource "aws_vpc" "v" {\n}\n'}, "db_encryption_at_rest", "missing"),
    ("backups kept 0 days", {"terraform/d.tf": DB % "  backup_retention_period = 0\n"}, "db_backups", "contradicted"),
    ("multi_az false", {"terraform/d.tf": DB % "  multi_az = false\n"}, "multi_az", "contradicted"),
    ("publicly accessible left to default", {"terraform/d.tf": DB % "  identifier = \"x\"\n"}, "db_not_public", "missing"),
    ("log retention 30 days",
     {"terraform/l.tf": 'resource "aws_cloudwatch_log_group" "g" {\n  retention_in_days = 30\n}\n'}, "log_retention", "contradicted"),
    ("password length 8",
     {"terraform/p.tf": 'resource "aws_iam_account_password_policy" "p" {\n  minimum_password_length = 8\n}\n'},
     "password_length", "contradicted"),
    ("TLS 1.0 policy on HTTPS listener",
     {"terraform/a.tf": LISTENER % ("HTTPS", '  ssl_policy = "ELBSecurityPolicy-TLS-1-0-2015-04"\n')}, "tls_in_transit", "contradicted"),
    ("TLS policy is a variable",
     {"terraform/a.tf": LISTENER % ("HTTPS", "  ssl_policy = var.policy\n")}, "tls_in_transit", "unverifiable"),
    ("HTTP listener forwards instead of redirecting",
     {"terraform/a.tf": LISTENER % ("HTTP", '  default_action {\n    type = "forward"\n  }\n')}, "https_redirect", "contradicted"),
    ("HTTP listener redirects to HTTP",
     {"terraform/a.tf": LISTENER % ("HTTP", '  default_action {\n    type = "redirect"\n    redirect {\n      protocol = "HTTP"\n'
                                                '      status_code = "HTTP_301"\n    }\n  }\n')}, "https_redirect", "contradicted"),
    ("parameter group exists but no database uses it",
     {"terraform/d.tf": DB % "  identifier = \"x\"\n",
      "terraform/g.tf": 'resource "aws_db_parameter_group" "g" {\n  parameter {\n    name = "rds.force_ssl"\n    value = "1"\n  }\n}\n'},
     "db_tls_required", "missing"),
    ("force_ssl set to 0",
     {"terraform/d.tf": DB % "  parameter_group_name = aws_db_parameter_group.g.name\n",
      "terraform/g.tf": 'resource "aws_db_parameter_group" "g" {\n  parameter {\n    name = "rds.force_ssl"\n    value = "0"\n  }\n}\n'},
     "db_tls_required", "contradicted"),
    ("database security group open to the world",
     {"terraform/d.tf": DB % "  vpc_security_group_ids = [aws_security_group.db.id]\n",
      "terraform/n.tf": 'resource "aws_security_group" "db" {\n  ingress {\n    cidr_blocks = ["0.0.0.0/0"]\n  }\n}\n'},
     "db_network_restricted", "contradicted"),
    ("extra ingress rule resource widens the database security group",
     {"terraform/d.tf": DB % "  vpc_security_group_ids = [aws_security_group.db.id]\n",
      "terraform/n.tf": 'resource "aws_security_group" "db" {\n  ingress {\n    security_groups = [aws_security_group.app.id]\n  }\n}\n'
                        'resource "aws_security_group_rule" "open" {\n  security_group_id = aws_security_group.db.id\n}\n'},
     "db_network_restricted", "unverifiable"),
    ("public load balancer without a WAF",
     {"terraform/a.tf": 'resource "aws_lb" "public" {\n  name = "x"\n}\n'}, "waf", "missing"),
    ("WAF attached to a different load balancer",
     {"terraform/a.tf": 'resource "aws_lb" "public" {\n}\nresource "aws_lb" "other" {\n}\n'
                        'resource "aws_wafv2_web_acl_association" "w" {\n  resource_arn = aws_lb.other.arn\n}\n'}, "waf", "missing"),
    ("auditor role also has admin access",
     {"terraform/i.tf": 'resource "aws_iam_role" "auditor" {\n}\n'
                        'resource "aws_iam_role_policy_attachment" "ro" {\n  role = aws_iam_role.auditor.name\n'
                        '  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"\n}\n'
                        'resource "aws_iam_role_policy_attachment" "admin" {\n  role = aws_iam_role.auditor.name\n'
                        '  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"\n}\n'}, "least_privilege", "contradicted"),
    ("flow log only rejects traffic",
     {"terraform/n.tf": 'resource "aws_vpc" "v" {\n}\nresource "aws_flow_log" "f" {\n  vpc_id = aws_vpc.v.id\n  traffic_type = "REJECT"\n}\n'},
     "network_flow_logs", "contradicted"),
    ("flow log is for a different vpc",
     {"terraform/n.tf": 'resource "aws_vpc" "v" {\n}\nresource "aws_flow_log" "f" {\n  vpc_id = aws_vpc.other.id\n  traffic_type = "ALL"\n}\n'},
     "network_flow_logs", "missing"),
    ("bucket versioning belongs to another bucket",
     {"terraform/s.tf": 'resource "aws_s3_bucket" "a" {\n}\nresource "aws_s3_bucket" "b" {\n}\n'
                        'resource "aws_s3_bucket_versioning" "a" {\n  bucket = aws_s3_bucket.a.id\n  versioning_configuration {\n'
                        '    status = "Enabled"\n  }\n}\n'}, "storage_versioning", "missing"),
    ("versioning suspended",
     {"terraform/s.tf": 'resource "aws_s3_bucket" "a" {\n}\nresource "aws_s3_bucket_versioning" "a" {\n  bucket = aws_s3_bucket.a.id\n'
                        '  versioning_configuration {\n    status = "Suspended"\n  }\n}\n'}, "storage_versioning", "contradicted"),
    ("only one of four public-access flags set",
     {"terraform/s.tf": 'resource "aws_s3_bucket" "a" {\n}\nresource "aws_s3_bucket_public_access_block" "a" {\n'
                        '  bucket = aws_s3_bucket.a.id\n  block_public_acls = true\n}\n'}, "storage_public_access_blocked", NOT_FOUND),
    ("bucket encryption config for another bucket",
     {"terraform/s.tf": 'resource "aws_s3_bucket" "a" {\n}\nresource "aws_s3_bucket" "b" {\n}\n'
                        'resource "aws_s3_bucket_server_side_encryption_configuration" "a" {\n  bucket = aws_s3_bucket.a.id\n'
                        '  rule {\n    apply_server_side_encryption_by_default {\n      sse_algorithm = "aws:kms"\n    }\n  }\n}\n'},
     "storage_encryption_all_buckets", "contradicted"),
    ("MFA policy defined but never attached",
     {"terraform/i.tf": MFA_TF.split("resource \"aws_iam_group")[0], "iam/mfa.json": MFA_JSON % ("Deny", "BoolIfExists")},
     "mfa_admin", "missing"),
    ("MFA policy that Allows", {"terraform/i.tf": MFA_TF, "iam/mfa.json": MFA_JSON % ("Allow", "BoolIfExists")}, "mfa_admin", "missing"),
    ("MFA policy using Bool, which skips sessions without the key",
     {"terraform/i.tf": MFA_TF, "iam/mfa.json": MFA_JSON % ("Deny", "Bool")}, "mfa_admin", "missing"),
    ("MFA policy JSON with a duplicated key",
     {"terraform/i.tf": MFA_TF, "iam/mfa.json": '{"Effect": "Deny", "Effect": "Allow", "Statement": []}'}, "mfa_admin", "unverifiable"),
    ("MFA policy file path escapes the repo",
     {"terraform/i.tf": MFA_TF.replace("../iam/mfa.json", "../../../etc/passwd")}, "mfa_admin", "missing"),
    # YAML / JSON / text
    ("mfa flag only in a comment", {"configs/auth.yaml": "# enforced_for_all_users: true\nmfa:\n  enforced_for_roles: [admin]\n"},
     "mfa_all_users", "missing"),
    ("mfa flag duplicated (true then false)",
     {"configs/auth.yaml": "mfa:\n  enforced_for_all_users: true\n  enforced_for_all_users: false\n"}, "mfa_all_users", "unverifiable"),
    ("mfa flag is the string 'true'", {"configs/auth.yaml": 'mfa:\n  enforced_for_all_users: "true"\n'}, "mfa_all_users", "contradicted"),
    ("mfa flag in a file that is not valid YAML", {"configs/auth.yaml": "mfa: [unclosed\n"}, "mfa_all_users", "unverifiable"),
    ("mfa flag under another key", {"configs/auth.yaml": "legacy:\n  enforced_for_all_users: true\n"}, "mfa_all_users", "missing"),
    ("sso provider without protocol", {"configs/auth.yaml": "sso:\n  provider: okta\n"}, "sso", "missing"),
    ("session timeout zero", {"configs/auth.yaml": "session_timeout_minutes: 0\n"}, "session_timeout", "contradicted"),
    ("session timeout is a boolean", {"configs/auth.yaml": "session_timeout_minutes: true\n"}, "session_timeout", "contradicted"),
    ("main branch requires zero reviews",
     {".github/settings.yml": "branches:\n  - name: main\n    protection:\n      required_pull_request_reviews:\n"
                              "        required_approving_review_count: 0\n"}, "code_review_required", "contradicted"),
    ("reviews required on a different branch",
     {".github/settings.yml": "branches:\n  - name: dev\n    protection:\n      required_pull_request_reviews:\n"
                              "        required_approving_review_count: 2\n"}, "code_review_required", "missing"),
    ("hsts header commented out", {"configs/n.conf": '# add_header Strict-Transport-Security "max-age=31536000";\n'}, "hsts", "missing"),
    ("hsts max-age zero", {"configs/n.conf": 'add_header Strict-Transport-Security "max-age=0";\n'}, "hsts", "contradicted"),
    ("security contact only in an HTML comment", {"SECURITY.md": "# Policy\n<!-- mail security@example.com -->\n"},
     "vulnerability_disclosure", "missing"),
    # CI
    ("codeql step in a disabled job", {".github/workflows/c.yml": WORKFLOW % ("push", "    if: false\n", CODEQL)}, "sast", "missing"),
    ("codeql step that never blocks nor runs",
     {".github/workflows/c.yml": WORKFLOW % ("push", "", CODEQL + "\n        if: false")}, "sast", "missing"),
    ("codeql step ignoring failure", {".github/workflows/c.yml": WORKFLOW % ("push", "", CODEQL + "\n        continue-on-error: true")},
     "sast", "missing"),
    ("codeql only on manual dispatch", {".github/workflows/c.yml": WORKFLOW % ("workflow_dispatch", "", CODEQL)}, "sast", "missing"),
    ("codeql named only in a comment", {".github/workflows/c.yml": WORKFLOW % ("push", "", "run: echo ok  # github/codeql-action/analyze@v3")},
     "sast", "missing"),
    ("look-alike action name", {".github/workflows/c.yml": WORKFLOW % ("push", "", "uses: github/codeql-action/analyze-evil@v3")},
     "sast", "missing"),
    ("dependabot with no schedule", {".github/dependabot.yml": "version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n"},
     "dependency_scanning", "missing"),
    # Good twins: the same shapes done right must still be found.
    ("GOOD db", {"terraform/d.tf": DB % "  storage_encrypted = true\n"}, "db_encryption_at_rest", "found"),
    ("GOOD mfa policy", {"terraform/i.tf": MFA_TF, "iam/mfa.json": MFA_JSON % ("Deny", "BoolIfExists")}, "mfa_admin", "found"),
    ("GOOD mfa flag", {"configs/auth.yaml": "mfa:\n  enforced_for_all_users: true\n"}, "mfa_all_users", "found"),
    ("GOOD codeql", {".github/workflows/c.yml": WORKFLOW % ("[push, pull_request]", "", CODEQL)}, "sast", "found"),
    ("GOOD hsts", {"configs/n.conf": 'add_header Strict-Transport-Security "max-age=31536000" always;\n'}, "hsts", "found"),
    ("GOOD contact", {"SECURITY.md": "Email security@example.com\n"}, "vulnerability_disclosure", "found"),
]


def repo_with(files: dict[str, str]) -> Path:
    repo = Path(tempfile.mkdtemp(prefix="trustdesk-adv-")) / "product"
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


class StructuredVerifier(unittest.TestCase):
    def test_cases(self):
        for name, files, control, expected in CASES:
            with self.subTest(name):
                got = search.check(control, repo_with(files))
                if expected == NOT_FOUND:
                    self.assertNotEqual(got["status"], "found", got)
                else:
                    self.assertEqual(got["status"], expected, got)

    def test_every_found_citation_is_the_real_line(self):
        for name, files, control, expected in CASES:
            if expected != "found":
                continue
            repo = repo_with(files)
            got = search.check(control, repo)
            for cite in got["evidence"]:
                loc, text = cite.split("  ", 1)
                path, line = loc.rsplit(":", 1)
                lines = (repo.parent / path).read_text(encoding="utf-8").splitlines()
                self.assertEqual(lines[int(line) - 1].strip(), text, name)

    def test_facts_name_the_resource_and_value(self):
        got = search.check("db_encryption_at_rest", repo_with({"terraform/d.tf": DB % "  storage_encrypted = true\n"}))
        fact = got["facts"][0]
        self.assertEqual((fact["subject"], fact["attr"], fact["value"]), ("aws_db_instance.main", "storage_encrypted", True))

    def test_unresolved_value_is_reported_as_expression_not_literal(self):
        got = search.check("tls_in_transit", repo_with({"terraform/a.tf": LISTENER % ("HTTPS", "  ssl_policy = var.policy\n")}))
        self.assertEqual(got["status"], "unverifiable")
        self.assertEqual(got["facts"][0]["value"], {"expression": "${var.policy}"})


if __name__ == "__main__":
    unittest.main()
