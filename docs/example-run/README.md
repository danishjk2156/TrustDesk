# Example run — 2026-11-05

One full TrustDesk run of `samples/acme_questionnaire.xlsx` (40 questions) in `acme-deal-room`,
Switch Console v0.40.1 on Windows 11.

| File | What it is |
| --- | --- |
| `acme_questionnaire_completed.xlsx` | The packager's output: the customer's sheet + Answer, Label, Evidence, Approved by |
| `evidence.md` | Evidence appendix: every answer with its `file:line` citation and approval time |
| `answers.json` | The drafter's answer records after all approvals |

Result: 30 VERIFIED, 8 NEEDS-EVIDENCE (including 2 policy claims contradicted by code: Q3 the
unencrypted `exports` bucket, Q9 admin-only MFA), 2 COMMITMENT approved by Legal (Q40 edited to
99.5%). All 40 match `tests/answer_key.json`.

Along the way the AE was refused by `@packager` and `@evidence-checker`, and the packager refused
to package until every approval was recorded.
