# Account Pool Refresh And Request Cache Rate Implementation Plan

> 历史计划：截至 2026-09-16，功能已实现并补齐回归验证，详见 [完成与验证记录](../../../account-pool/REFRESH_CACHE_RATE_VERIFICATION.md)。下文保留原始计划和当时的复选框，不代表当前完成状态。旧缓存率分母已修正为输入 token 总数；用户最新要求使用中文提交并推送，取代下文“不推送”的约束

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scheduled and immediate authentication-file refresh, surface quota refresh controls on the dashboard, and show a cache-input ratio on every request log row

**Architecture:** Reuse the existing persisted account-pool settings and periodic quota scheduler pattern for an independent authentication refresh loop. Keep quota refresh details in the quota panel while exposing the same backend refresh status and action in the dashboard. Compute each request's cache ratio from its immutable usage fields and expose it through manager, LiteLLM proxy, and dashboard contracts

**Tech Stack:** Python 3.11, FastAPI, Pydantic, asyncio, PostgreSQL, React 19, TypeScript, TanStack Query, Vitest

**Spec:** User-approved chat requirements from 2026-09-16

## Global Constraints

- Authentication refresh interval options are exactly 5, 15, 30, and 60 minutes, defaulting to 15 minutes
- Authentication refresh must fetch upstream authentication or cookie state without requesting a live quota refresh
- The credentials page must provide both the interval selector and an immediate refresh action
- The dashboard must add only the quota immediate-refresh action, last refresh time, and next refresh time to its existing content
- Per-request cache rate is `cache_read_input_tokens / (input_tokens + cache_read_input_tokens + cache_creation_input_tokens)` and is unknown when the denominator is zero or usage is absent
- Existing aggregate cache statistics remain compatible
- Human-facing copy and the final conventional commit subject are Chinese
- Commit locally and do not push

---

### Task 1: Per-request cache rate contract

**Files:**
- Modify: `account-pool/account_pool/error_logs.py`
- Modify: `account-pool/account_pool/gateway_service.py`
- Modify: `litellm/proxy/management_endpoints/account_pool_management_models.py`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolLogsPanel.tsx`
- Modify: `ui/litellm-dashboard/src/locales/zh-CN.json`
- Modify: `ui/litellm-dashboard/src/locales/en.json`
- Test: `account-pool/tests/account_pool/test_gateway_service.py`
- Test: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolLogsPanel.test.tsx`

**Interfaces:**
- Produces: `ErrorLogRecord.cache_rate: float | None`
- Formula: cache-read tokens divided by all input token categories for that final request attempt

- [ ] Write a failing gateway-service test asserting a successful request with 20 uncached, 80 cache-read, and 0 cache-created input tokens records `cache_rate == 0.8`, plus an absent-usage case recording `None`
- [ ] Run the focused Python test and verify the field is missing
- [ ] Add the immutable field and compute it while constructing the request event
- [ ] Run the focused Python test and verify it passes
- [ ] Write a failing log-panel test asserting each request row renders its cache percentage
- [ ] Run the focused Vitest test and verify the column is missing
- [ ] Mirror the field in the LiteLLM proxy contract, add the table column and localized labels, then rerun the focused test

### Task 2: Authentication refresh scheduler and API

**Files:**
- Modify: `account-pool/account_pool/settings.py`
- Modify: `account-pool/account_pool/quota_scheduler.py`
- Modify: `account-pool/account_pool/service.py`
- Modify: `account-pool/account_pool/app.py`
- Modify: `account-pool/account_pool/api.py`
- Modify: `litellm/proxy/management_endpoints/account_pool_endpoints.py`
- Test: `account-pool/tests/account_pool/test_quota.py`
- Test: `account-pool/tests/test_account_pool.py`
- Test: mapped LiteLLM account-pool endpoint test

**Interfaces:**
- Produces: persisted `auth_refresh_interval_minutes: Literal[5, 15, 30, 60] = 15`
- Produces: manager and proxy endpoints `POST /auth-files/refresh`, `GET /auth-files/refresh/status`, and `PUT /auth-files/refresh/interval`
- Produces: authentication refresh status with interval, running, last completion, next run, and failure count

- [ ] Write failing scheduler and service tests proving the default is 15 minutes and automatic or immediate authentication refresh calls account reads with `refresh_quota=False`
- [ ] Run the focused tests and verify failures are caused by the missing setting and operations
- [ ] Generalize the existing periodic scheduler only enough to support independent quota and authentication schedules, then add the service operation and app lifecycle task
- [ ] Run the focused tests and verify they pass
- [ ] Write failing manager and LiteLLM proxy endpoint tests for status, interval update, and immediate refresh
- [ ] Run those tests and verify the endpoints are absent
- [ ] Add typed request and response models and proxy forwarding, then rerun the focused tests

### Task 3: Credentials refresh controls

**Files:**
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolManagementApi.ts`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCredentialsPanel.tsx`
- Modify: `ui/litellm-dashboard/src/locales/zh-CN.json`
- Modify: `ui/litellm-dashboard/src/locales/en.json`
- Test: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCredentialsPanel.integration.test.tsx`
- Test: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolManagementApi.test.ts`

**Interfaces:**
- Consumes: authentication refresh endpoints from Task 2
- Produces: credentials-page interval selector and immediate refresh button with progress and refresh timestamps

- [ ] Write a failing integration test for the 5/15/30/60 selector, default 15-minute status, and immediate refresh action
- [ ] Run the focused test and verify the controls or API calls are missing
- [ ] Add typed API functions and wire the controls to mutations and query invalidation
- [ ] Rerun focused UI tests and verify they pass

### Task 4: Dashboard quota shortcut

**Files:**
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolDashboard.tsx`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx`
- Modify: `ui/litellm-dashboard/src/locales/zh-CN.json`
- Modify: `ui/litellm-dashboard/src/locales/en.json`
- Test: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/accountPoolDashboardSelectors.test.ts`
- Test: appropriate dashboard component test, creating `AccountPoolDashboard.test.tsx` only if no mapped component test exists

**Interfaces:**
- Consumes: existing quota status and immediate refresh APIs
- Produces: dashboard additions for immediate refresh, last completion, and next scheduled refresh

- [ ] Write a failing component test for the three dashboard additions and refresh action
- [ ] Run it and verify the additions are absent
- [ ] Load quota status in the page, pass it with the existing refresh action into the dashboard, and render only the approved additions
- [ ] Rerun the focused dashboard tests and verify they pass

### Task 5: Verification and local commit

**Files:**
- Modify only files required by Tasks 1 through 4

**Interfaces:**
- Consumes: all prior tasks
- Produces: one verified local commit, no remote upload

- [ ] Run focused account-pool Python tests
- [ ] Run focused dashboard unit, component, and integration tests
- [ ] Run relevant lint and type checks, updating lint budgets only if this change lowers a gated ceiling
- [ ] Inspect the complete diff for secrets, generated artifacts, unrelated files, and accidental comments
- [ ] Create one conventional Chinese commit such as `feat(account-pool): 增加认证刷新和请求缓存率` without pushing
