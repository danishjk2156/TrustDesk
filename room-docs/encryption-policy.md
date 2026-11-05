# Encryption policy (AcmeCloud)

Owner: Security Lead · Reviewed: 2026-09-01

1. All customer data is encrypted at rest with AES-256 using AWS KMS customer-managed keys.
   This covers the production database and **every S3 bucket that holds customer data**.
2. KMS keys rotate automatically every year.
3. All traffic to the platform is encrypted in transit with TLS 1.2 or higher.
   Plain HTTP is redirected to HTTPS, and browsers are told to use HTTPS only (HSTS).
4. Connections from the application to the database require TLS.
5. Encryption keys are never stored in source code.
