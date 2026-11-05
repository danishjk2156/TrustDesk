# acmecloud (sample product repo)

A small, fake SaaS infrastructure repo that TrustDesk's evidence-checker searches for proof.
It is seeded on purpose:

- about 30 security controls are provable from the files here,
- 6 controls are deliberately missing (no backup restore tests, no pentest, ...),
- 2 controls contradict what the policies claim (one S3 bucket without encryption,
  MFA enforced for admins only).

Nothing here is deployed; it only has to look like a real Terraform + config repo.
