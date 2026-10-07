# Controlled OAuth Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator complete OAuth in a temporary server-hosted browser whose only upstream egress is the proxy selected for the account.

**Architecture:** Manager owns persisted browser-session state, proxy binding, ticket validation, Docker lifecycle, and callback relay. LiteLLM keeps admin authorization and proxies session APIs and the browser WebSocket; the Dashboard only presents the session and manual browser controls. OAuth state validation and token exchange remain in the existing Manager and CLIProxyAPI flow.

**Tech Stack:** Existing FastAPI Manager, Docker Compose runtime, LiteLLM management routes, React Dashboard, and the repository's existing pytest/Vitest checks. The worker image and every dependency must be pinned by digest after a real Docker build and isolation test.

**Spec:** `account-pool/docs/controlled-oauth-browser.md`

## Global Constraints

- The browser session is temporary, single-use, and mutually exclusive per account environment.
- Store only a hash of its expiring browser ticket; never return Manager credentials or persist browser cookies in Manager state.
- Keep worker containers off published host ports and away from Docker socket, databases, Manager tokens, and other account networks.
- Browser egress must fail closed through the immutable proxy selection captured when the OAuth session starts.
- Preserve existing provider callback URI, state validation, single-consumption, and CLIProxyAPI verification behavior.
- Never record screenshots, keystrokes, cookies, page bodies, OAuth codes, or proxy credentials in logs.
- Run focused tests before broader tests; real Docker and supplier OAuth results must be reported separately from mocks.

---

### Task 1: Persisted Browser Session Contract

**Files:**
- Modify: `account-pool/account_pool/domain.py`
- Modify: `account-pool/account_pool/repository.py`
- Modify: `account-pool/account_pool/ports.py`
- Create: `account-pool/account_pool/oauth_browser.py`
- Test: `account-pool/tests/account_pool/test_oauth_browser.py`

**Deliverable:** A typed session model and repository operations that enforce one active session per environment, expiry, one-time ticket use, and atomic cancellation without persisting the raw ticket.

- [x] Write tests for one active session per environment, ticket hash matching, expiry rejection, replay rejection, and atomic cancellation.
- [x] Run `python -m pytest account-pool/tests/account_pool/test_oauth_browser.py -q` and confirm the missing contract fails.
- [x] Implement only the session model, repository protocol, schema, and application service needed by those tests.
- [x] Re-run the focused test and existing Manager authorization/repository tests.
- [x] Commit as `feat(account-pool): persist controlled oauth browser sessions`.

### Task 2: Isolated Worker Runtime

**Files:**
- Modify: `account-pool/account_pool/config.py`
- Modify: `account-pool/account_pool/compose_renderer.py`
- Modify: `account-pool/account_pool/compose_runtime.py`
- Create: `account-pool/browser-worker/Dockerfile`
- Create: `account-pool/browser-worker/entrypoint.py`
- Test: `account-pool/tests/account_pool/test_browser_worker_runtime.py`

**Deliverable:** A short-lived, unprivileged browser worker with no published port or Docker socket, attached only to an internal browser network and a narrowly scoped egress relay that uses the captured selected proxy. The worker is not enabled in account Compose until the digest and fail-closed behavior pass a real Docker check.

- [x] Write renderer/runtime tests asserting no published ports, no Docker socket, read-only root filesystem, session-scoped cleanup, and no direct worker egress path.
- [x] Run the focused test and confirm the worker service/runtime is absent or violates the expected contract.
- [ ] Build the worker image, resolve and pin its digest, and test that proxy outage blocks upstream access while the selected proxy works.
- [x] Implement the minimum worker and runtime lifecycle, then re-run renderer/runtime and cleanup tests.
- [x] Commit as `feat(account-pool): run isolated oauth browser workers`.

### Task 3: Manager Session and Callback APIs

**Files:**
- Modify: `account-pool/account_pool/application/environments/authorization.py`
- Modify: `account-pool/account_pool/service.py`
- Modify: `account-pool/account_pool/api.py`
- Test: `account-pool/tests/account_pool/test_management.py`
- Test: `account-pool/tests/account_pool/test_account_pool.py`

**Deliverable:** Authenticated Manager routes to start, inspect, and cancel a session, expose only an expiring one-time browser ticket, bind the worker to the current proxy selection, and relay only the provider callback fields into existing OAuth state validation.

- [x] Write API tests for manager authentication, environment ownership, duplicate sessions, proxy changes, callback replay, redaction, timeout, and worker cleanup.
- [x] Run the focused tests and verify they fail for the unimplemented session routes.
- [x] Implement route/service orchestration using the persisted session service and existing `submit_oauth_callback` path.
- [x] Re-run Manager OAuth and browser-session tests.
- [x] Commit as `feat(account-pool): expose controlled oauth browser sessions`.

### Task 4: LiteLLM and Dashboard Integration

**Files:**
- Modify: the existing account-pool management endpoint and tests under `litellm/proxy/management_endpoints/` and `tests/test_litellm/proxy/management_endpoints/`
- Modify: `ui/litellm-dashboard/src/features/account-pool/api/AccountPoolManagementApi.ts`
- Modify: `ui/litellm-dashboard/src/features/account-pool/components/credentials/AccountPoolAuthorizationPanel.tsx`
- Modify: existing account-pool authorization tests under `ui/litellm-dashboard/src/features/account-pool/`

**Deliverable:** The Dashboard starts and cancels the server browser using existing admin credentials, shows a temporary same-origin browser surface, and retains the existing device-code flow and manual CAPTCHA/MFA interaction.

- [x] Add LiteLLM forwarding and Dashboard tests for ticket handling, same-origin browser access, expiry, cancellation, and no ticket leakage into persisted UI state.
- [x] Run those focused tests and confirm they fail before implementation.
- [x] Implement the smallest API and UI changes, including an authenticated WebSocket relay.
- [x] Run the focused LiteLLM tests, Dashboard tests, and production frontend build.
- [x] Commit as `feat(account-pool): integrate controlled oauth browser ui`.

### Task 5: End-to-End Security Verification

**Files:**
- Modify: `account-pool/docs/controlled-oauth-browser.md`
- Test: existing browser-session and Docker integration test locations established by Tasks 1-4.

**Deliverable:** Fresh evidence for cleanup, proxy binding, no direct fallback, session concurrency, callback state consumption, and manual human login. Documentation status reflects only what the tests and real services demonstrate.

- [ ] Run the complete Account Pool Manager test suite and LiteLLM management test suite.
- [ ] Run the Dashboard focused tests, source type checks, and production build.
- [ ] Start the worker with real Docker and verify its network, mounts, capabilities, port bindings, proxy outage behavior, and cleanup.
- [ ] Complete one supplier OAuth using the manual browser, CAPTCHA/MFA, callback relay, and CLIProxyAPI verification path.
- [ ] Update the design document with exact commands, results, remaining deployment requirements, and implementation status.
- [ ] Review the complete diff and commit the final verification record.
