"""Build the 40-question demo questionnaire and its private answer key.

    python tools/make_sample.py

Writes:
  samples/acme_questionnaire.xlsx  what the AE uploads (ID, Question, Category)
  tests/answer_key.json            what each answer should end up as; never given to agents

Mix: 30 provable from acmecloud/, 6 missing, 2 contradicted by the code, 2 commitments.
"""
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

ROOT = Path(__file__).resolve().parent.parent

# (id, question, category, control tag or None, expected outcome)
QUESTIONS = [
    ("Q1", "Is customer data in your production database encrypted at rest?", "Encryption", "db_encryption_at_rest", "found"),
    ("Q2", "Are encryption keys rotated automatically?", "Encryption", "kms_key_rotation", "found"),
    ("Q3", "Is all customer data stored in object storage (e.g. S3) encrypted at rest?", "Encryption", "storage_encryption_all_buckets", "contradicted"),
    ("Q4", "Is data in transit encrypted with TLS 1.2 or higher?", "Encryption", "tls_in_transit", "found"),
    ("Q5", "Are plain HTTP requests redirected to HTTPS?", "Encryption", "https_redirect", "found"),
    ("Q6", "Do you send an HSTS header on your web application?", "Encryption", "hsts", "found"),
    ("Q7", "Are connections between your application and database encrypted?", "Encryption", "db_tls_required", "found"),
    ("Q8", "Is multi-factor authentication required for administrative access?", "Access control", "mfa_admin", "found"),
    ("Q9", "Is multi-factor authentication enforced for all workforce users?", "Access control", "mfa_all_users", "contradicted"),
    ("Q10", "Do employees sign in through single sign-on (SSO)?", "Access control", "sso", "found"),
    ("Q11", "Do you enforce a minimum password length of at least 12 characters?", "Access control", "password_length", "found"),
    ("Q12", "Do you prevent reuse of previous passwords?", "Access control", "password_reuse", "found"),
    ("Q13", "Do administrative sessions time out after inactivity?", "Access control", "session_timeout", "found"),
    ("Q14", "Is access granted on a least-privilege basis (e.g. read-only roles for auditors)?", "Access control", "least_privilege", "found"),
    ("Q15", "Are production code changes peer-reviewed before merge?", "Secure development", "code_review_required", "found"),
    ("Q16", "Do you keep an audit log of administrative actions across all regions?", "Logging", "audit_logging", "found"),
    ("Q17", "Are audit logs protected against tampering?", "Logging", "log_integrity", "found"),
    ("Q18", "Are logs retained for at least 12 months?", "Logging", "log_retention", "found"),
    ("Q19", "Do you capture network flow logs for your production network?", "Logging", "network_flow_logs", "found"),
    ("Q20", "Do you take automated backups of customer data?", "Business continuity", "db_backups", "found"),
    ("Q21", "Is your production database deployed across multiple availability zones?", "Business continuity", "multi_az", "found"),
    ("Q22", "Is the production database protected against accidental deletion?", "Business continuity", "deletion_protection", "found"),
    ("Q23", "Is versioning enabled on storage that holds customer files?", "Business continuity", "storage_versioning", "found"),
    ("Q24", "Is public access blocked on all storage buckets?", "Network security", "storage_public_access_blocked", "found"),
    ("Q25", "Is a web application firewall (WAF) in front of your application?", "Network security", "waf", "found"),
    ("Q26", "Is your production database isolated from the public internet?", "Network security", "db_not_public", "found"),
    ("Q27", "Is database access restricted to the application tier only?", "Network security", "db_network_restricted", "found"),
    ("Q28", "Do you run static application security testing (SAST) on every change?", "Secure development", "sast", "found"),
    ("Q29", "Do you monitor third-party dependencies for known vulnerabilities?", "Secure development", "dependency_scanning", "found"),
    ("Q30", "Do you scan code for committed secrets?", "Secure development", "secrets_scanning", "found"),
    ("Q31", "Are container images scanned for vulnerabilities before deployment?", "Secure development", "container_scanning", "found"),
    ("Q32", "Do you have a public channel for reporting security vulnerabilities?", "Secure development", "vulnerability_disclosure", "found"),
    ("Q33", "Do you test restoring from backups at least annually?", "Business continuity", "backup_restore_testing", "missing"),
    ("Q34", "Has an independent third party performed a penetration test in the last 12 months?", "Assurance", "penetration_testing", "missing"),
    ("Q35", "Is your disaster recovery plan tested at least annually?", "Business continuity", "dr_testing", "missing"),
    ("Q36", "Is endpoint protection (EDR/antivirus) installed on all employee devices?", "Endpoint security", "endpoint_protection", "missing"),
    ("Q37", "Do you delete customer data on request and at contract termination?", "Privacy", "data_deletion", "missing"),
    ("Q38", "Do employees with access to customer data undergo background checks?", "Human resources", "background_checks", "missing"),
    ("Q39", "Within how many hours will you notify us after confirming a data breach?", "Incident response", None, "commitment"),
    ("Q40", "What monthly uptime do you commit to for the production service?", "Service levels", None, "commitment"),
]

xlsx = ROOT / "samples" / "acme_questionnaire.xlsx"
xlsx.parent.mkdir(parents=True, exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = "Security Questionnaire"
ws.append(["ID", "Question", "Category"])
for cell in ws[1]:
    cell.font = Font(bold=True)
for qid, q, cat, _, _ in QUESTIONS:
    ws.append([qid, q, cat])
ws.column_dimensions["A"].width = 6
ws.column_dimensions["B"].width = 80
ws.column_dimensions["C"].width = 22
for row in ws.iter_rows(min_row=2):
    for cell in row:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
wb.save(xlsx)

key = ROOT / "tests" / "answer_key.json"
key.parent.mkdir(parents=True, exist_ok=True)
key.write_text(json.dumps(
    [{"id": qid, "question": q, "control": ctl, "expected": exp} for qid, q, _, ctl, exp in QUESTIONS],
    indent=2), encoding="utf-8")

counts = {}
for *_, exp in QUESTIONS:
    counts[exp] = counts.get(exp, 0) + 1
print(xlsx)
print(key)
print(counts)
