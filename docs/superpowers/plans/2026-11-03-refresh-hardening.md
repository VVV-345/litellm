# CPA refresh hardening

## Accepted scope

Keep CPA as the sole refresh owner. Do not add a Manager/LiteLLM refresh scheduler, alter refresh lead defaults, rotate live credentials, deploy, publish, or push. Work on `CLIProxyAPI分支` and preserve unrelated working-tree edits. The adapter stays pinned to its existing upstream commit and is changed through the existing verified patch

Source review: `D:/Work2api/litellm/.local/sub2api-refresh-review-20261103/REVIEW.md`

## Implementation and acceptance

1. Propagate refresh persistence failure, retain the already-rotated credential in the running process, and retry persistence without calling OAuth again. Ensure a successful file save is atomic. Verify forced save failure, recovery without a second upstream refresh, cancellation handling, and successful restart loading
2. Reuse the existing per-auth coordinator for automatic, explicit management, and request-triggered refresh. Recheck the current credential under the lock, and protect new imports/updates from a stale refresh completion or stale error. Verify overlapping refresh requests, concurrent reauthorization/disable/removal, genuine invalid credentials, and current-state scheduling
3. Package each coherent fix into the adapter patch and update its verified SHA-256. Verify application to a pristine pinned checkout, focused regression suites, broader affected packages, server build, and existing management-auth gates. Commit coherent fixes separately; do not push

Do not turn a transient storage/network failure into proof that an access token is revoked. Do not suppress a genuine upstream authentication failure or clear unrelated quota/model/manual-disable state. Keep secrets out of logs, errors, reports and Git

## Boundaries

An upstream refresh and local storage are not a single distributed transaction. Retaining a new credential in memory protects a running process, but cannot guarantee crash recovery if all durable writes fail. Report this boundary honestly; do not retry the consumed refresh token or claim permanent unattended access

Sub2API is a behavioral reference, not code to copy. In particular, do not adopt its Redis-lock fail-open policy or imply its Grok-only credential CAS covers OpenAI

## Evidence and rollback

Use the existing four-role paths under `D:/Work2api/litellm/.local/quota-reset-20261102/transaction`. Preserve their previous versions in a history folder before updating the roles. Run identical synthetic-credential checks against baseline, modified and separately restored copies. Reopen all four roles and verify restored hashes before reporting

Production authentication recovery requires fresh valid credentials and separate live verification. Local tests do not establish that either existing account works
