# PR #1 ISP subscription adaptation and v1.7

## Approved scope

User approved adapting and fixing PR #1, setting the product version to `v1.7`, and pushing the verified result

Source PR: https://github.com/VVV-345/litellm/pull/1
Pinned head: `eb84682a24c445e4eeb21ce5961f3167fbb4f0e6`
Starting local commit: `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`
Working branch: `CLIProxyAPI分支`; no new branch or worktree

Existing Cockpit identity adaptation, authorization scope, quota switching, continuation and replay boundaries remain unchanged. Existing user untracked files are outside this task

## Compatibility decisions

Keep the original Clash configuration source and ordinary airport gateway mode. Keep manual ISP records and bindings. Chain mode requires an explicitly selected airport front and fails closed when incomplete; do not automatically choose an airport or bypass the ISP. Existing ISP-bound ports therefore need a front selection before chain activation, as explained and approved in the conversation

Fix the shared subscription downloader's public-destination validation and streamed byte limit, gateway deletion compensation ordering, and mixed-source batch probing. Do not replace the account-pool engine or introduce a new proxy service

Push is authorized, but direct server deployment is not. The successful version tag is created by the existing image publication workflow after its gates, not before successful publication

## Task 1: baseline and integration checkpoint

- [x] Verify a clean tracked tree, switch to the specified branch, fast-forward pull and fetch the pinned PR head
- [x] Record the existing Manager proxy, LiteLLM API and Dashboard proxy test results
- [x] Preserve pristine affected bytes and reuse the four transaction roles under `.local/cockpit-adaptation/transaction`
- [x] Commit this scoped plan after baseline checks (`d7a9dcf7e9`)

## Task 2: merge and backend fixes

Files: `account-pool/account_pool/proxy_gateways.py`, existing proxy gateway and PostgreSQL tests, and the PR's ISP catalog modules

- [x] Start a non-committing merge of the pinned PR head, retaining the existing identity fields
- [x] Add failing tests for hostnames resolving to non-public addresses, redirects, oversized streamed responses and failure recovery with foreign-key-constrained bindings
- [x] Validate and pin the connection destination while preserving HTTP Host and TLS server-name verification; stop reading immediately at the byte limit
- [x] Restore a deleted port before restoring its ISP/front bindings when deletion fails
- [x] Verify ordinary airport mode, explicit chain activation, incomplete-chain rejection, independent gateway bindings and configuration restoration

## Task 3: Dashboard and API coexistence

Files: `ISPProxyCatalogPanel.tsx`, its existing integration test, `AccountPoolApi` and the generated schema

- [x] Add a failing mixed manual/subscription-source batch detection test
- [x] Probe only manual records in the catalog batch action; retain per-gateway probing for subscription chains
- [x] Preserve `identity_fingerprint_mode=managed` and `identity_confuse` alongside the new proxy APIs
- [x] Generate schema with `npm run gen:api`, never hand-edit it
- [ ] Run the explicit proxy/API and identity settings integration tests, then commit the verified merge and fixes

## Task 4: final verification, rollback and release

- [x] Run focused Manager proxy/catalog/probe/release tests, LiteLLM management/session/conversation/quota failover tests, UI tests, source typing and relevant lint
- [x] Run real PostgreSQL checks in a disposable local database if the runtime is available; do not use production data
- [x] Verify fail-closed, config parsing and routing behavior with local test fixtures; no live ISP/airport/provider acceptance is claimed
- [x] Extend the same modified archive, reconstructing patch, evidence ledger and executable rollback with this task's affected files
- [ ] Execute identical baseline/modified/rollback probes on disposable copies and verify pristine restored hashes
- [x] Set `deploy/VERSION` to `v1.7`, record the exact verified scope and remaining live-service gates, and prepare the release-stage commit
- [ ] Confirm staged content and branch, push without force, inspect the matching image workflow and report its actual result

## Required evidence

Keep commands, inputs, literal stdout/stderr and exit statuses. Historical or skipped checks are not fresh passes. No claim that an OpenAI 401 proves model completion, and no claim of live ISP egress without observing it

The existing four roles remain the only delivery roles; supplemental logs and controller metadata extend them

## Baseline evidence

At `086c0f391a`, Manager's six proxy files: 65 passed, 2 PostgreSQL checks skipped; LiteLLM management API: 56 passed; the two explicit Dashboard proxy/API files: 24 passed. Python emitted an existing Starlette deprecation warning; Vitest emitted existing configuration/type-testing notices

The Docker client exists but its Linux-engine named pipe is unavailable (observed exit 1). No production database or server was contacted. Disposable PostgreSQL availability is being checked separately

## Integration evidence

The current Manager proxy/config/catalog/probe scope passes 121 tests against disposable PostgreSQL 17.6. Failed deletion restores manual, subscription and inactive-subscription bindings; normal API binding still rejects inactive nodes. Invalid configuration shapes leave the active file unchanged

The release runtime had an existing `ReleasePair`/`ReleaseImage` argument error in its additive schema path. Four regression cases reproduced the `AttributeError` before the fix. The runtime now reads each pair's Manager sources and LiteLLM Prisma; it permits the exact ISP additions and rejects changed Prisma, extra DDL and modified DDL. The release suite passes 200 tests after this fix

Other observed checks: Manager identity/settings 143 passed; LiteLLM management/session/conversation/failover 220 passed; Dashboard proxy/API 32 passed; Dashboard identity settings 26 passed. Generated API types, isolated Dashboard source typing and the webpack production build pass. UI lint exits zero with 31 warnings. The scoped strict Manager proxy-module type check still reports 33 errors, down from 65 in the original isolated source; this is not a passing type gate

Read-only review found no remaining P1/P2 integration blocker. The additive DDL gate is an exact schema allowlist, not a complete source-digest audit. No live ISP/airport/model-provider acceptance or production deployment has been performed
