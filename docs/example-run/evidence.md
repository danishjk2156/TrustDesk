# Evidence appendix — acme_questionnaire.xlsx
Generated 2026-11-05 22:41 · Released by security-lead

| ID | Question | Answer | Evidence | Approved |
|----|----------|--------|----------|----------|
| Q1 | Is customer data in your production database encrypted at rest? | Yes. Customer data in the production database is encrypted at rest with AES-256 using AWS KMS customer-managed keys. | `acmecloud/terraform/rds.tf:18` storage_encrypted = true | security-lead, 2026-11-05 22:36 |
| Q2 | Are encryption keys rotated automatically? | Yes. KMS keys rotate automatically every year. | `acmecloud/terraform/kms.tf:4` enable_key_rotation     = true | security-lead, 2026-11-05 22:36 |
| Q4 | Is data in transit encrypted with TLS 1.2 or higher? | Yes. All traffic to the platform is encrypted in transit with TLS 1.2 or higher. | `acmecloud/terraform/alb.tf:12` ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06" | security-lead, 2026-11-05 22:36 |
| Q5 | Are plain HTTP requests redirected to HTTPS? | Yes. Plain HTTP is redirected to HTTPS. | `acmecloud/terraform/alb.tf:31` status_code = "HTTP_301" | security-lead, 2026-11-05 22:36 |
| Q6 | Do you send an HSTS header on your web application? | Yes. Browsers are told to use HTTPS only (HSTS). | `acmecloud/configs/nginx.conf:5` add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always; | security-lead, 2026-11-05 22:36 |
| Q7 | Are connections between your application and database encrypted? | Yes. Connections from the application to the database require TLS. | `acmecloud/terraform/rds.tf:6` name  = "rds.force_ssl" | security-lead, 2026-11-05 22:36 |
| Q8 | Is multi-factor authentication required for administrative access? | Yes. MFA is always required for administrative access. | `acmecloud/iam/mfa_policy.json:10` "BoolIfExists": { "aws:MultiFactorAuthPresent": "false" } | security-lead, 2026-11-05 22:36 |
| Q10 | Do employees sign in through single sign-on (SSO)? | Yes. Workforce sign-in uses single sign-on through Okta. | `acmecloud/configs/auth.yaml:3` provider: okta | security-lead, 2026-11-05 22:36 |
| Q11 | Do you enforce a minimum password length of at least 12 characters? | Yes. Passwords must be at least 14 characters. | `acmecloud/terraform/iam.tf:2` minimum_password_length        = 14 | security-lead, 2026-11-05 22:36 |
| Q12 | Do you prevent reuse of previous passwords? | Yes. The last 24 passwords cannot be reused. | `acmecloud/terraform/iam.tf:7` password_reuse_prevention      = 24 | security-lead, 2026-11-05 22:36 |
| Q13 | Do administrative sessions time out after inactivity? | Yes. Console sessions time out after 30 minutes of inactivity. | `acmecloud/configs/auth.yaml:7` session_timeout_minutes: 30 | security-lead, 2026-11-05 22:36 |
| Q14 | Is access granted on a least-privilege basis (e.g. read-only roles for auditors)? | Yes. Access follows least privilege; auditors get read-only roles. | `acmecloud/terraform/iam.tf:19` policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess" | security-lead, 2026-11-05 22:36 |
| Q15 | Are production code changes peer-reviewed before merge? | Yes. Production changes go through reviewed pull requests with at least one approval. | `acmecloud/.github/settings.yml:6` required_approving_review_count: 1 | security-lead, 2026-11-05 22:36 |
| Q16 | Do you keep an audit log of administrative actions across all regions? | Yes. All administrative API activity is recorded in a multi-region audit trail. | `acmecloud/terraform/logging.tf:4` is_multi_region_trail         = true | security-lead, 2026-11-05 22:36 |
| Q17 | Are audit logs protected against tampering? | Yes. The audit trail has log file integrity validation. | `acmecloud/terraform/logging.tf:6` enable_log_file_validation    = true | security-lead, 2026-11-05 22:36 |
| Q18 | Are logs retained for at least 12 months? | Yes. Application and network logs are kept for 365 days. | `acmecloud/terraform/logging.tf:11` retention_in_days = 365<br>`acmecloud/terraform/logging.tf:17` retention_in_days = 365 | security-lead, 2026-11-05 22:36 |
| Q19 | Do you capture network flow logs for your production network? | Yes. VPC flow logs are enabled for the production network. | `acmecloud/terraform/network.tf:6` resource "aws_flow_log" "main" { | security-lead, 2026-11-05 22:36 |
| Q20 | Do you take automated backups of customer data? | Yes. The database keeps 35 days of automated backups. | `acmecloud/terraform/rds.tf:21` backup_retention_period = 35 | security-lead, 2026-11-05 22:36 |
| Q21 | Is your production database deployed across multiple availability zones? | Yes. The database runs across multiple availability zones. | `acmecloud/terraform/rds.tf:23` multi_az                = true | security-lead, 2026-11-05 22:36 |
| Q22 | Is the production database protected against accidental deletion? | Yes. Deletion protection is enabled on the production database. | `acmecloud/terraform/rds.tf:24` deletion_protection     = true | security-lead, 2026-11-05 22:36 |
| Q23 | Is versioning enabled on storage that holds customer files? | Yes. Versioning is enabled on customer file storage. | `acmecloud/terraform/s3.tf:38` status = "Enabled"<br>`acmecloud/terraform/s3.tf:45` status = "Enabled" | security-lead, 2026-11-05 22:36 |
| Q24 | Is public access blocked on all storage buckets? | Yes. Public access is blocked on all storage buckets. | `acmecloud/terraform/s3.tf:51` block_public_acls       = true<br>`acmecloud/terraform/s3.tf:59` block_public_acls       = true<br>`acmecloud/terraform/s3.tf:67` block_public_acls       = true | security-lead, 2026-11-05 22:36 |
| Q25 | Is a web application firewall (WAF) in front of your application? | Yes. A web application firewall is attached to the public load balancer. | `acmecloud/terraform/alb.tf:36` resource "aws_wafv2_web_acl_association" "public" { | security-lead, 2026-11-05 22:36 |
| Q26 | Is your production database isolated from the public internet? | Yes. The production database is not reachable from the internet. | `acmecloud/terraform/rds.tf:25` publicly_accessible     = false | security-lead, 2026-11-05 22:36 |
| Q27 | Is database access restricted to the application tier only? | Yes. The database accepts connections only from the application tier. | `acmecloud/terraform/network.tf:27` security_groups = [aws_security_group.app.id] | security-lead, 2026-11-05 22:36 |
| Q28 | Do you run static application security testing (SAST) on every change? | Yes. Static analysis (CodeQL) runs in CI. | `acmecloud/.github/workflows/ci.yml:23` - uses: github/codeql-action/analyze@v3 | security-lead, 2026-11-05 22:36 |
| Q29 | Do you monitor third-party dependencies for known vulnerabilities? | Yes. Dependencies are monitored with Dependabot. | `acmecloud/.github/dependabot.yml:3` - package-ecosystem: npm<br>`acmecloud/.github/dependabot.yml:7` - package-ecosystem: terraform | security-lead, 2026-11-05 22:36 |
| Q30 | Do you scan code for committed secrets? | Yes. Secret scanning (gitleaks) runs in CI. | `acmecloud/.github/workflows/ci.yml:31` - uses: gitleaks/gitleaks-action@v2 | security-lead, 2026-11-05 22:36 |
| Q31 | Are container images scanned for vulnerabilities before deployment? | Yes. Container images are scanned (Trivy) in CI. | `acmecloud/.github/workflows/ci.yml:38` - uses: aquasecurity/trivy-action@0.24.0 | security-lead, 2026-11-05 22:36 |
| Q32 | Do you have a public channel for reporting security vulnerabilities? | Yes. Vulnerabilities can be reported to security@acmecloud.example. | `acmecloud/SECURITY.md:5` Email security@acmecloud.example with the details and steps to reproduce. | security-lead, 2026-11-05 22:36 |

## Commitments (approved by Legal)
- Q39: "72 hours: affected customers are notified within 72 hours of AcmeCloud confirming a personal-data breach." (legal, 2026-11-05 22:39)
- Q40: "99.5% monthly availability for the production platform, excluding announced maintenance. Service credits apply below that." (legal, 2026-11-05 22:39)

## Not evidenced
- Q3: Partially: 2 of 3 buckets are encrypted; the exports bucket is not. Reason: 1 of 3 S3 buckets have no encryption configuration: exports. Code: acmecloud/terraform/s3.tf:9 resource "aws_s3_bucket" "exports" { (security-lead, 2026-11-05 22:36)
- Q9: No. MFA is required for administrative access but is not enforced for all workforce users. Reason: The code says the opposite of: mfa enforced for every workforce user. Code: acmecloud/configs/auth.yaml:11 enforced_for_all_users: false (security-lead, 2026-11-05 22:36)
- Q33: Unknown: no document or record of backup restore testing. Reason: Not provable from code: backup restore testing; needs a document or record. (security-lead, 2026-11-05 22:36)
- Q34: Unknown: no document or record of independent penetration test. Reason: Not provable from code: independent penetration test; needs a document or record. (security-lead, 2026-11-05 22:36)
- Q35: Unknown: no document or record of disaster recovery testing. Reason: Not provable from code: disaster recovery testing; needs a document or record. (security-lead, 2026-11-05 22:36)
- Q36: Unknown: no document or record of endpoint protection on staff devices. Reason: Not provable from code: endpoint protection on staff devices; needs a document or record. (security-lead, 2026-11-05 22:36)
- Q37: Unknown: no document or record of customer data deletion on request or contract end. Reason: Not provable from code: customer data deletion on request or contract end; needs a document or record. (security-lead, 2026-11-05 22:36)
- Q38: Unknown: no document or record of background checks for staff. Reason: Not provable from code: background checks for staff; needs a document or record. (security-lead, 2026-11-05 22:36)
