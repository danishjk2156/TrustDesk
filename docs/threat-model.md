# TrustDesk threat model

TrustDesk's product is trust: every VERIFIED answer must be true, and every approval must come from
the person who owns it. Two failures block release:

1. **False-positive VERIFIED**: an answer labelled VERIFIED for a control the code does not satisfy.
2. **Unauthorized approval**: an answer released without an authenticated approval from the right role,
   or released after the answer or its evidence changed.

## Assets

| Asset | Why it matters |
|---|---|
| Completed questionnaire | Sent to a customer; a wrong "Yes" is a contractual and legal statement. |
| Answer library | Reused across deals; a poisoned entry spreads. |
| Approval ledger | The record that a human accepted a specific answer. |
| Product repo and its token | Remediation writes to it; the token must not reach further. |
| Customer spreadsheets | Confidential, and untrusted input. |

## Actors and trust

| Actor | Trusted for | Not trusted for |
|---|---|---|
| Customer spreadsheet | Nothing; it is data. | Instructions, formulas, links, sizes. |
| Drafter / checker / red-team / remediator / packager models | Producing drafts. | Evidence verdicts, approvals, gate decisions. |
| AE | Uploading questionnaires. | Approving or releasing. |
| Security Lead | Approving facts. | Approving promises. |
| Legal | Approving promises (COMMITMENT). | Approving facts. |
| Engineer | Reviewing remediation PRs. | Approving answers. |

## Attacks and the control that stops each

| Attack | Control | Test |
|---|---|---|
| Commented-out or wrong-resource setting passes as proof | Structured, resource-level verification; unresolved values are `unverifiable` | `test_adv_false_positives.py`, `test_adv_structured.py` |
| A real but irrelevant `file:line` cited as proof | Gate re-runs the verifier; control must fit the question; citations must be that control's proof; numbers must be supported | `test_adv_claims.py` |
| Model writes `approved_by` itself | `approved_by` is never read; approvals are signed ledger entries from a pinned roster, role-checked | `test_adv_approvals.py`, `test_gates.py` |
| Forged, replayed, expired, future-dated or revoked approval | Ed25519 check, per-deal and per-answer binding, 30-day expiry, clock-skew limit, revoke entries | `test_adv_approvals.py` |
| Edited roster adds an approver | Roster hash pinned outside the repo; gate fails closed without the pin | `test_adv_approvals.py`, `test_cli_flow.py` |
| GitHub review from a fork, unmerged PR, or a commit changed after review | Repo pinned in the roster; review must be the merged head and an ancestor of `main` | `test_adv_approvals.py` |
| Answer edited after approval | Approval bound to a digest of answer, claims, evidence and file hashes (policy hashes for COMMITMENT) | `test_adv_approvals.py`, `test_adv_stale.py` |
| Code changes after approval | Evidence re-proven at the gate; file hashes in the digest; drift check in CI | `test_adv_stale.py`, `test_drift.py` |
| Instructions hidden in the spreadsheet, customer files or product repo | Treated as data; flagged on normalised text; never reach a verdict, approval or tool call | `test_adv_prompt_injection.py`, `test_adv_xlsx.py` |
| Formula or DDE in a cell, hyperlinks, defined names | Output written as text on every sheet; links, names, validations and conditional formats removed | `test_adv_xlsx.py` |
| Zip bomb, macros, external links, hidden sheets | Size, entry, ratio, row and column caps; macro/link/embedding/ActiveX refused | `test_adv_xlsx.py` |
| One deal reads another's data, or leaks into the library | Per-deal workdir, deal-scoped MCP tools, library scrubber with keyed deal label | `test_adv_isolation.py` |
| Over-broad CI or remediator token | Per-job read-only `permissions`, SHA-pinned actions, token preflight, fine-grained credentials | `test_workflows.py`, `test_preflight.py` |
| A faulty remediation recipe weakens security | Independent validation, rollback, strict tool mode | `test_adv_remediation.py` |

## Rule

Anything the verifier cannot resolve (a variable, a module, `count`, an unknown control) is
`unverifiable`, never `found`. The system fails closed.
