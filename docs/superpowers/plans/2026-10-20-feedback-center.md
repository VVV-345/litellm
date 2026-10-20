# Feedback Center Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, with isolated task assistance where required by the skill; do not create a Git worktree.

**Goal:** Ship persistent admin feedback and harness-independent read/claim/result write-back.
**Architecture:** Dedicated Manager repository/domain/API, LiteLLM authenticated bridge and limited machine bridge, reusable dashboard module, standard-library CLI.
**Tech Stack:** PostgreSQL/psycopg, FastAPI/Pydantic, React Query/Base UI, Python stdlib.
**Spec:** docs/superpowers/specs/2026-10-20-feedback-center-design.md

## Global Constraints
- Work on CLIProxyAPI分支; no new Git branch/worktree or deployment. Original tracked bytes are archived before changing the candidate copy.
- Preserve gateway routing and existing features. Scoped feedback token must never authorize another management route.
- One canonical harness workflow, no tool-specific business logic. JSON CLI output; do not execute feedback contents.

### Task 1: Manager domain, PostgreSQL persistence and API
Files: account_pool/feedback_models.py, feedback.py, feedback_repository.py, feedback_api.py; compose in app.py; tests/account_pool/test_feedback.py and test_feedback_api.py.
- [ ] Write red tests proving blank input rejection, CAS conflict, ownership, required evidence, expiry/revocation and full state transitions. `assert submit_without_evidence.status == 422`.
- [ ] Run selected pytest tests using the root .venv interpreter and the candidate PYTHONPATH; preserve stdout/stderr/exit codes.
- [ ] Implement strict models, domain result values, parameterized SQL with CAS, hash-only token persistence and safe API errors.
- [ ] Run tests again, plus existing app construction checks.

### Task 2: LiteLLM bridge and generated types
Files: account_pool_feedback_models.py, account_pool_feedback.py, account_pool_endpoints.py; corresponding test_account_pool_feedback.py.
- [ ] Write red tests: non-admin rejected, agent cannot access admin create/delete, expired tokens fail, forwarded errors retain code, canonical CRUD round trip.
- [ ] Implement allowlisted resource routes and trusted admin actor; include router without touching request routing.
- [ ] Generate UI OpenAPI types with LITELLM_PYTHON pointing to root .venv; never hand-edit schema.d.ts.

### Task 3: Dashboard feedback module
Files under features/account-pool/components/feedback and api/AccountPoolFeedbackApi.ts; wire DashboardHeader.tsx, account-pool/page.tsx, lazy modules and query keys as needed.
- [ ] Write red component tests for create/edit/delete, state/count display, error retention and token one-time display.
- [ ] Implement explicit-save form, paginated list with server filtering, state actions, token issuance/revocation and administrator-only header entry.
- [ ] Run only relevant Vitest files and ESLint; verify production source TypeScript.

### Task 4: Portable CLI and shared harness workflow
Files scripts/feedback.py, tests/local_testing/test_feedback_cli.py, docs/FEEDBACK_WORKFLOW.md; append references to AGENTS.md/CLAUDE.md/GEMINI.md.
- [ ] Exercise CLI as a subprocess against a local HTTP fixture. It must send X-Feedback-Token without printing it, preserve Unicode in JSON, refuse redirects and fail nonzero for 401/409/network errors.
- [ ] Implement stdlib client and actions exactly matching the spec.
- [ ] Document initial admin issuance, environment setup, pending reads, claim, fix and verification result write-back, reopening and permission limits.

### Task 5: Integrated verification and commit
- [ ] Review all touched code and the acceptance checklist; fix only related defects.
- [ ] Run regression tests, targeted Ruff/ESLint/TypeScript and available PostgreSQL HTTP integration.
- [ ] Package MODIFIED_FILE.zip, reconstruct it using DIFF_FILE.patch, run portable ROLLBACK.sh on a separate copy and compare restored SHA256.
- [ ] Run identical new-feature probe on baseline/modified/rollback copies, preserve literal output and status; reapply the changed package.
- [ ] Reopen four roles, sync only approved changed files to root, rerun root checks and commit; push only as required by repository instructions and record results.
