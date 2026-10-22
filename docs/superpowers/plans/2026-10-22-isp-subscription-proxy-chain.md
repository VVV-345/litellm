# ISP Subscription Proxy Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import one ISP Clash subscription into the existing ISP directory, bind ISP and airport nodes per gateway, and route each gateway through the airport into the ISP node.

**Architecture:** Keep manual ISP rows and their existing binding table compatible. Store one encrypted ISP subscription and its full encrypted Mihomo node configs in additive Manager tables, merge both sources in the existing ISP catalog contract, and keep subscription bindings in a source-specific table. Generate a per-gateway ISP outbound with `dialer-proxy` set to a per-gateway airport selector; keep the listener's gateway selector limited to that outbound and `REJECT`.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL via psycopg, PyYAML, httpx, Mihomo configuration YAML, LiteLLM management routes, React, TanStack Query, generated OpenAPI types, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-isp-subscription-proxy-chain-design.md`

## Global Constraints

- Work directly on `CLIProxyAPI分支`; do not create a branch or worktree.
- Retain manual ISP import, editing, expiration, existing API paths, and manual profile IDs.
- Import only the top-level Clash `proxies` array; do not recursively read `proxy-providers`.
- Store the ISP subscription URL and complete subscription node configurations with the existing `StateCipher`.
- Never log or return the subscription URL, ISP credentials, full node configs, or upstream response bodies.
- Reject duplicate node names and malformed or oversized subscriptions before saving.
- A gateway with an unavailable ISP or missing airport must resolve to `REJECT`; never fall back to direct or airport-only egress.
- Bindings are per gateway. A shared ISP profile may produce a separate generated ISP node for each bound gateway.
- Subscription refresh keeps IDs and bindings for matching node names. Missing nodes become unavailable and block their bound gateway; a later same-name node reuses its ID.
- Subscription refresh, airport configuration replacement, binding, and gateway deletion must preserve or reconcile all generated groups and nodes.
- Any failed Clash configuration reload restores the previous file and reloads the restored configuration.
- Link checks use the selected gateway listener. An HTTP 401 from OpenAI proves endpoint/TLS reachability only, not a successful model call.
- New Manager schema uses idempotent additive DDL only. It does not alter Prisma or rewrite existing rows; register reviewed new table contracts in `release_compatibility.py`.
- Dashboard API types in `src/lib/http/schema.d.ts` are generated with `npm run gen:api`, never edited by hand.
- Write and run a failing behavior test before production changes, then run the focused test after the implementation.

---

## File Map

- `account-pool/account_pool/isp_subscription.py`: validate a downloaded ISP Clash document and return immutable named Mihomo proxy entries with the complete validated node config.
- `account-pool/account_pool/proxy_catalog.py`: add tagged manual/subscription profile fields and safe public views while preserving current manual profile fields.
- `account-pool/account_pool/proxy_catalog_repository.py`: add encrypted singleton subscription storage, encrypted stable subscription-node storage, source-specific ISP bindings, and one front-node binding per port.
- `account-pool/account_pool/proxy_catalog_service.py`: import/refresh the single subscription, merge profiles, preserve matching IDs, and mark missing subscription nodes unavailable.
- `account-pool/account_pool/proxy_gateways.py`: generate per-port airport selectors and ISP chain proxies, reconcile gateway state, and probe a selected gateway.
- `account-pool/account_pool/isp_probe.py`: provide a gateway-listener probe that reports exit-IP/ISP results and OpenAI HTTP reachability without an API key.
- `account-pool/account_pool/api.py`, `account-pool/account_pool/service.py`, `account-pool/account_pool/app.py`: register Manager request/response models, routes, service calls, dependency injection, startup reconciliation, and gateway removal cleanup.
- `account-pool/account_pool/release_compatibility.py`: allow only the exact new additive schema contracts.
- `account-pool/tests/account_pool/test_isp_profiles.py`, `test_proxy_catalog_schema.py`, `test_proxy_catalog_postgres.py`, `test_proxy_gateways.py`, `test_isp_probe.py`: parser, schema, persistence, chain, rollback, and link-probe behavior.
- `account-pool/tests/test_release_runtime.py`: verify the new schema remains narrowly allow-listed.
- `litellm/proxy/management_endpoints/account_pool_endpoints.py`, `account_pool_proxy_catalog.py`: add administrator-authenticated forwarding for ISP subscription, per-gateway front selection, and gateway link probe.
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py`: verify route methods, payloads, admin authorization, and redaction.
- `ui/litellm-dashboard/src/features/account-pool/api/AccountPoolApi.ts`, `hooks/useProxyGateways.ts`, `components/proxy/ISPProxyCatalogPanel.tsx`, `components/proxy/ProxyManagerPanel.tsx`, `utils/AccountPoolTypes.ts`: expose subscription management and per-gateway front selection and probe results.
- `ui/litellm-dashboard/src/locales/zh-CN.json`, `en.json`: add localized labels for source, subscription state, front node, and link-check results.
- `ui/litellm-dashboard/src/lib/http/schema.d.ts`: regenerate from the proxy OpenAPI schema.
- `ui/litellm-dashboard/src/features/account-pool/components/proxy/ProxyManagerPanel.integration.test.tsx`, `api/AccountPoolApi.test.ts`: cover user-visible binding and generated request contracts.

## Task 1: Validate ISP Clash Subscription Nodes

**Files:**
- Create: `account-pool/account_pool/isp_subscription.py`
- Modify: `account-pool/tests/account_pool/test_isp_profiles.py`

**Interfaces:**
- Produces `MihomoISPNode(name: str, proxy_type: str, config: JsonValue)` with the full node mapping in `config`.
- Produces `parse_isp_subscription(content: str) -> tuple[MihomoISPNode, ...]`.
- Rejects non-object YAML, missing/empty top-level `proxies`, invalid nodes, duplicate names, non-string config keys, and values outside JSON-compatible scalars/lists/maps. It ignores nested provider definitions.

- [x] Add parser cases for a valid top-level node with protocol-specific nested fields, a nested `proxy-providers` entry that must not be imported, duplicate names, empty proxies, invalid YAML, malformed node fields, and a document over 2 MiB.
- [x] Run `uv run --project account-pool --extra test --locked python -m pytest account-pool/tests/account_pool/test_isp_profiles.py -q` and confirm each new assertion fails because the parser is missing.
- [x] Implement the frozen typed node model and parser with `yaml.safe_load`; preserve every validated node field in `config` and avoid rendering input values in validation errors.
- [x] Re-run the focused ISP test file; result: `21 passed`.

## Task 2: Persist Both ISP Sources in One Catalog Contract

**Files:**
- Modify: `account-pool/account_pool/proxy_catalog.py`
- Modify: `account-pool/account_pool/proxy_catalog_repository.py`
- Modify: `account-pool/account_pool/proxy_catalog_service.py`
- Modify: `account-pool/account_pool/release_compatibility.py`
- Modify: `account-pool/tests/account_pool/test_proxy_catalog_schema.py`
- Modify: `account-pool/tests/account_pool/test_proxy_catalog_postgres.py`
- Modify: `account-pool/tests/account_pool/test_isp_profiles.py`
- Modify: `account-pool/tests/test_release_runtime.py`

**Interfaces:**
- Extend the public ISP view with `source: Literal["manual", "subscription"]` and `proxy_type: str | None`; subscription entries expose no raw config or credentials.
- Keep `list_isps`, `get_isp`, `bind_isp`, `unbind_isp`, `list_isp_bindings`, and `list_inactive_isp_bindings` as the common catalog operations for both sources.
- Add subscription state read/save and node synchronization operations; a matching node name retains its UUID, a removed node retains its row as disabled, and a restored node name reuses the disabled row.
- Store the single URL under a stable cipher identity and each full node config under its stable node UUID. Store subscription bindings and gateway front-node names in separate tables.
- Manager subscription routes are `GET /api/proxy-isps/subscription`, `PUT /api/proxy-isps/subscription` with `{ "url": "https://..." }`, and `POST /api/proxy-isps/subscription/refresh` with no body.
- Manager front-node selection is `PUT /api/proxy-gateways/{port}/front` with `{ "node_name": "..." }`; clearing uses `{ "node_name": null }`. Gateway link probing is `POST /api/proxy-gateways/{port}/probe`.

- [x] Add tests proving imported subscription nodes appear beside manual rows with source labels, views do not expose URL/config secrets, bindings list both sources, and one port cannot retain both source bindings.
- [x] Add PostgreSQL integration coverage for encrypted URL/config storage, same-name UUID retention, missing-node disablement, same-name recovery, front-node persistence, and idempotent schema initialization.
- [x] Run the focused ISP and schema tests before repository changes; the missing-table and source-field assertions failed as expected.
- [x] Add only `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` statements. Update the reviewed compatibility allow-list and its tests with exact normalized DDL bytes.
- [x] Implement source-aware list/get/delete/bind operations behind the existing repository protocol. Make `unbind_isp(port)` remove both source-specific rows atomically within one database connection.
- [x] Implement refresh as one repository synchronization operation that upserts present node configs, disables missing rows, and leaves gateway bindings intact.
- [x] Run the focused ISP/schema/PostgreSQL/release test files; result: `24 passed, 3 skipped`. PostgreSQL tests skipped because `PROXY_CATALOG_TEST_DATABASE_URL` is unset. Subscription service and URL validation tests: `2 passed`.

## Task 3: Build and Reconcile a Per-Gateway Proxy Chain

**Files:**
- Modify: `account-pool/account_pool/proxy_gateways.py`
- Modify: `account-pool/account_pool/clash.py` only if a typed node filter is needed
- Modify: `account-pool/tests/account_pool/test_proxy_gateways.py`

**Interfaces:**
- Extend `GatewayView` with `front_node_name: str | None` while preserving existing fields.
- Add `bind_front_node(port: int, node_name: str | None) -> GatewayView` and `probe_gateway(port: int) -> GatewayISPProbeResult` on the gateway service.
- Each gateway owns a front selector named `account-pool-front-{port}`. The existing `clash-gateway-{port}` selector contains only that port's generated ISP node and `REJECT` while ISP mode is bound.
- A generated chain proxy copies a manual proxy's current Mihomo mapping or the full subscription mapping, then overrides only `name` and `dialer-proxy`.

- [x] Add tests for two ports sharing one ISP but using different airport nodes, complete protocol-field retention, exact `dialer-proxy` override, missing airport blocking, hidden generated ISP nodes in airport choices, subscription node disappearance/recovery, airport config replacement, gateway removal, and reload rollback. Added regressions for provider-backed selectors and deleting the last gateway.
- [x] Run `python -m pytest account-pool/tests/account_pool/test_proxy_gateways.py -q` and confirm the new chain assertions fail against current single-hop behavior.
- [x] Add a front selector for every registered gateway and retain its choice through ordinary airport configuration updates. Its choices may include only existing egress airport nodes, never generated ISP nodes, selectors, `DIRECT`, or `REJECT` as the selected node.
- [x] Generate one unique ISP chain proxy per `(gateway port, ISP profile ID)`, apply its `dialer-proxy`, and limit the gateway selector to that proxy plus `REJECT`. If the ISP is missing/disabled, or the front is missing/unavailable, select `REJECT`.
- [x] Reconcile all registered gateway selectors and generated proxies after startup and subscription refresh. Preserve unrelated Clash configuration and restore the original file/runtime config if write or reload fails.
- [x] Remove the gateway front group, generated per-port proxy, ISP/front bindings, and saved port registration during gateway deletion; preserve the existing account-reference rejection.
- [x] Run the focused gateway tests and confirm all chain and failure-state assertions pass.

## Task 4: Probe the Actual Chained Gateway and Expose Manager APIs

**Files:**
- Modify: `account-pool/account_pool/isp_probe.py`
- Modify: `account-pool/account_pool/proxy_gateways.py`
- Modify: `account-pool/account_pool/api.py`
- Modify: `account-pool/account_pool/service.py`
- Modify: `account-pool/account_pool/app.py`
- Modify: `account-pool/tests/account_pool/test_isp_probe.py`
- Modify: `account-pool/tests/account_pool/test_proxy_gateways.py`

**Interfaces:**
- `GatewayISPProbeResult` includes `port`, checked time, IP probe outcome and public exit IP/location/ISP fields, plus OpenAI response outcome and HTTP status.
- `POST /api/proxy-gateways/{port}/probe` runs through `_gateway_proxy_url(port)`; HTTP 401 is reported as an OpenAI HTTP response, not as an authenticated model-call success.
- Add Manager routes to configure or import the single ISP subscription, read its non-secret status, and manually refresh its saved URL. Keep the existing manual `/api/proxy-isps` API operational.

- [x] Add probe tests with an injected HTTP client factory that records the gateway proxy URL, returns an exit IP and HTTP 401 from OpenAI, and separately covers IP failure, OpenAI network failure, timeout, and an unbound gateway.
- [x] Add Manager route tests for `GET/PUT /api/proxy-isps/subscription`, `POST /api/proxy-isps/subscription/refresh`, `PUT /api/proxy-gateways/{port}/front`, and `POST /api/proxy-gateways/{port}/probe`, without leaking request URL, node credentials, full config, or upstream response text.
- [x] Run `python -m pytest account-pool/tests/account_pool/test_isp_probe.py account-pool/tests/account_pool/test_proxy_gateways.py -q` and confirm new assertions fail before implementation.
- [x] Inject the probe into `ProxyGatewayService`; use the gateway listener URL for both IP lookup and `https://api.openai.com/v1/models`, with `trust_env=False`, redirects disabled, bounded timeouts, and bounded response parsing.
- [x] Add typed Manager requests/views and admin-protected routes. Return generic safe errors for subscription fetch or validation failure; do not include upstream response bodies.
- [x] Wire route methods through `EnvironmentService` and inject the shared catalog and gateway services in `app.py`. Ensure startup and refresh both reconcile inactive/missing bound nodes to `REJECT`.
- [x] Run the focused Manager tests and confirm the API contract and probe semantics pass.

## Task 5: Forward Admin APIs Through LiteLLM

**Files:**
- Modify: `litellm/proxy/management_endpoints/account_pool_endpoints.py`
- Modify: `litellm/proxy/management_endpoints/account_pool_proxy_catalog.py`
- Modify: `tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py`

**Interfaces:**
- Existing proxy-admin authorization applies to all new routes.
- LiteLLM validates Manager responses against typed Pydantic adapters and returns only the public catalog/probe views.
- Public paths are `/account_pool/proxy-isps/subscription`, `/account_pool/proxy-isps/subscription/refresh`, `/account_pool/proxy-gateways/{port}/front`, and `/account_pool/proxy-gateways/{port}/probe`.

- [x] Add route-forwarding cases for subscription status/configure/refresh, front binding, and per-gateway probe; assert method, Manager path, body, and parsed result.
- [x] Add view-only denial cases and malformed/error upstream responses. Assert no URL, credentials, raw node config, or upstream error body reaches the client.
- [x] Run `python -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py -q` and confirm the new route cases fail before forwarding code was added.
- [x] Add Pydantic contracts and routes to the existing management routers, preserving admin checks and generic upstream errors.
- [x] Re-run the focused LiteLLM management endpoint test file and confirm it passes.

## Task 6: Add ISP Subscription and Gateway Chain Controls to Dashboard

**Files:**
- Modify: `ui/litellm-dashboard/src/features/account-pool/api/AccountPoolApi.ts`
- Modify: `ui/litellm-dashboard/src/features/account-pool/hooks/useProxyGateways.ts`
- Modify: `ui/litellm-dashboard/src/features/account-pool/components/proxy/ISPProxyCatalogPanel.tsx`
- Modify: `ui/litellm-dashboard/src/features/account-pool/components/proxy/ProxyManagerPanel.tsx`
- Modify: `ui/litellm-dashboard/src/features/account-pool/utils/AccountPoolTypes.ts`
- Modify: `ui/litellm-dashboard/src/locales/zh-CN.json`
- Modify: `ui/litellm-dashboard/src/locales/en.json`
- Modify: `ui/litellm-dashboard/src/features/account-pool/components/proxy/ProxyManagerPanel.integration.test.tsx`
- Modify: `ui/litellm-dashboard/src/features/account-pool/api/AccountPoolApi.test.ts`
- Generate: `ui/litellm-dashboard/src/lib/http/schema.d.ts` via `npm run gen:api`

**Interfaces:**
- Subscription UI allows one URL, import/update, explicit manual refresh, and displays configured state/last refresh without echoing the stored URL.
- The shared ISP list labels `manual` versus `subscription`; only manual profiles show protocol/address/credential/expiration editing.
- Each gateway retains the ISP selector and gets a separate airport selector when ISP mode is selected. A gateway link probe shows exit ISP/IP and OpenAI HTTP outcome.
- TanStack Query invalidates the shared ISP, gateway, node, and probe keys after mutations.

- [x] Add integration assertions for source labels, URL configure and refresh requests, separate ISP/front selectors, two gateways retaining independent front nodes, missing-node blocked state, and a 401 OpenAI response shown as endpoint reachable.
- [x] Add API client assertions for new paths, methods, and request bodies; use generated OpenAPI schema types.
- [x] Run only `ProxyManagerPanel.integration.test.tsx` and `AccountPoolApi.test.ts` and confirm the new assertions fail before UI implementation.
- [x] Add typed API wrappers, query/mutation hooks, one-source import/refresh controls, and the per-gateway airport selector and probe result.
- [x] Add concise English and Chinese locale values for all new visible states; preserve the existing form, loading, error, and mutation feedback patterns.
- [x] Run `npm run gen:api` in `ui/litellm-dashboard`; do not hand-edit `schema.d.ts`.
- [x] Re-run the two focused Vitest files and run the dashboard source type check. Vitest type checks report no errors; standalone `tsc --noEmit` reports existing repository-wide unresolved imports and unrelated test/resource diagnostics.

## Task 7: Regressions and End-to-End Verification

**Files:**
- Verify touched backend, LiteLLM management, and Dashboard tests from Tasks 1-6.
- Update: `docs/superpowers/plans/2026-10-22-isp-subscription-proxy-chain.md` checkboxes with observed results.

- [x] Run the focused Manager suite: `python -m pytest account-pool/tests/account_pool/test_isp_profiles.py account-pool/tests/account_pool/test_proxy_catalog_schema.py account-pool/tests/account_pool/test_proxy_catalog_postgres.py account-pool/tests/account_pool/test_proxy_gateways.py account-pool/tests/account_pool/test_isp_probe.py account-pool/tests/account_pool/test_proxy_configuration.py -q`: `89 passed, 3 skipped`.
- [x] Run release-compatibility coverage: `python -m pytest account-pool/tests/test_release_runtime.py account-pool/tests/test_release_database.py -q`: `80 passed`.
- [x] Run LiteLLM management forwarding coverage: `python -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py -q`: `58 passed`.
- [x] Run the two touched Dashboard Vitest files and the Dashboard source type check; verify generated API types are in sync: `30 passed`, Vitest reports no type errors, `npm run gen:api` succeeds.
- [x] Inspect the complete diff for unintended files, plaintext secrets, logging of upstream bodies, config fallback to `DIRECT`, generated type edits outside `npm run gen:api`, and changes to unrelated routing/retry behavior. Dashboard lint reports zero errors and 31 warnings; Prettier and CRLF-aware `git diff --check` pass.
- [x] Record checks that could not run, including PostgreSQL URL availability and any GitHub network failure. Do not claim real OpenAI/GPT model verification without a credentialed live request. PostgreSQL integration tests skipped because `PROXY_CATALOG_TEST_DATABASE_URL` is unset. No credentialed live GPT request was run.
- [x] Commit the implementation on `CLIProxyAPI分支` after all available checks pass; push only if the remote is reachable, and report a connection failure explicitly. Commit: `cdee4e3500`.
