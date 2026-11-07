# Security hardening

What TrustDesk enforces in code, and what has to be set up outside it. The attack-to-control map is in
[threat-model.md](threat-model.md).

## Enforced in code

| Area | Control | Where |
| --- | --- | --- |
| Evidence | Terraform/YAML/JSON are parsed; unresolvable input is `unverifiable`, never `found` | `agents/evidence/` |
| Claims | Control must fit the question; citations must be that control's proof; numbers must be supported | `tools/claims.py` |
| Approvals | Signed (Ed25519) or merged-PR GitHub review, from the roster, in the right role, for this exact answer + evidence digest | `tools/approvals.py` |
| Release | Fails closed without a pinned roster hash | `tools/check_gates.py` |
| Remediation | Declared files only, Terraform append-only, no dangling reference, no weaker control, `terraform fmt`, `checkov`; rolled back on any failure | `tools/remediation_check.py` |
| Spreadsheets | Macros, external links, embeddings, zip bombs, hidden sheets refused; invisible characters stripped; formulas written as text | `tools/xlsx_safety.py` |
| Customer data | One folder per deal; library entries scrubbed of names, e-mail, IPs, foreign URLs | `tools/deals.py`, `tools/scrub.py` |
| CI | Actions pinned by commit SHA, read-only tokens, no `pull_request_target` | `.github/workflows/`, `tests/test_workflows.py` |

## Set up once, outside the repo

1. **Pin the approver roster.** Compute `sha256sum room-docs/approvers.yaml` and store it where agents cannot write
   (a CI secret, or the approver's shell): `TRUSTDESK_ROSTER_SHA256=<hash>`. Update it only through a reviewed change.
   `CODEOWNERS` already routes edits to the roster, gate and approval code to the owners; replace `@OWNER`.
2. **Approvers hold their own keys.** `python tools/approvals.py keygen --out ~/.trustdesk/me.key`, then put the printed
   public key in the roster. The drafting agents never see a private key.
3. **GitHub reviews (optional).** Set `github_repo: owner/name` in the roster. Only reviews on a PR merged into `main`
   whose final commit is the reviewed commit count; run the gate with `--verify-github`.
4. **Branch ruleset on `main`:** require pull requests, CODEOWNERS review, and the `Release blockers` status check; block
   force pushes. Without this, the checks above are advice.

## Credentials

Run `python tools/preflight.py` before the remediator or any `--verify-github` run. It refuses classic tokens (`ghp_`) and
tokens with `repo`, `workflow`, `admin:*` or package-write scopes, and never prints the token.

| Use | Credential | Permissions |
| --- | --- | --- |
| Remediator (opens a PR) | GitHub App installation token or fine-grained PAT, **this repository only** | Contents: write, Pull requests: write, Metadata: read. No Actions, Administration or Workflows. |
| Gate with `--verify-github` | Fine-grained PAT or App token | Contents: read, Pull requests: read |
| Everything else | None | The evidence engine, gate and drift check work from the checkout |

Limit where the remediator may push with a ruleset that allows creating `trustdesk/*` branches only. It never merges.

## Customer isolation

Work in `work/<deal-id>/` (override the root with `TRUSTDESK_WORK`). `tools/deals.py` rejects ids that are not
`[a-z0-9_-]`, paths that leave the folder and symlinks into another deal. The MCP tools take `deal_id` and refuse an answers
file from a different deal. Before a library PR, `tools/scrub.py --customer <name>` is mandatory (see the packager prompt).

## Known limits

- `pip install -r requirements.txt` is not hash-locked; use `pip-compile --generate-hashes` for production.
- Look-alike letters from other scripts survive Unicode normalisation, so a homoglyph instruction can evade the flag
  (the gate does not depend on the flag).
- Modules (`module` blocks) are not followed: anything that depends on one is `unverifiable`.
- No live AWS state is read: the proof is the code in the repo, not what is deployed.
