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
| Commented-out or wrong-resource setting passes as proof | Structured, resource-level verification; unresolved values are `unverifiable` | `tests/test_adv_false_positives.py` |
| A real but irrelevant `file:line` cited as proof | Gate re-runs the verifier and binds evidence to the claim | planned |
| Model writes `approved_by` itself | Approvals in a separate ledger, authenticated, role-checked | planned |
| Answer edited after approval | Approval bound to a digest of answer and evidence | planned |
| Code changes after approval | Digest includes evidence content hash; drift invalidates | planned |
| Instructions hidden in the spreadsheet | Treated as data; flagged; never reach a tool call | `tests/test_search.py` (Q41), more planned |
| Formula or DDE in a cell | Input rejected or neutralised; output escaped | planned |
| Zip bomb, macros, external links | Size caps; `.xlsm` and external links rejected | planned |
| One deal reads another's data | Per-deal workdir; deal-scoped tool paths | planned |
| Over-broad CI or remediator token | Least-privilege `permissions`, pinned actions, scoped token | planned |

## Rule

Anything the verifier cannot resolve (a variable, a module, `count`, an unknown control) is
`unverifiable`, never `found`. The system fails closed.
