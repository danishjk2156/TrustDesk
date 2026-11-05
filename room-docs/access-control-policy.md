# Access control policy (AcmeCloud)

Owner: Security Lead · Reviewed: 2026-09-01

1. Workforce sign-in uses single sign-on through Okta.
2. **Multi-factor authentication is required for all workforce users**, and always for
   administrative access.
3. Passwords are at least 14 characters, and the last 24 cannot be reused.
4. Console sessions time out after 30 minutes of inactivity.
5. Access follows least privilege: auditors get read-only roles; production changes go
   through reviewed pull requests with at least one approval.
6. The production database is not reachable from the internet and accepts connections
   only from the application tier.
