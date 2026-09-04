# Account Pool Channels and Suppliers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an extensible channel layer and support five real CLIProxyAPI suppliers while preserving every existing Codex record and Docker resource, with FreeBuff2API exposed only as a disabled placeholder

**Architecture:** Persist immutable channel and supplier identities on each environment, with defaults that map historical records to CLIProxyAPI and OpenAI Codex. A static channel registry delegates runtime and management work to CLIProxyAPI, whose supplier definitions hold the exact authorization endpoint, callback behavior, credential key, model-exclusion key, and quota parser. LiteLLM continues to consume a protocol-explicit gateway snapshot, while the dashboard selects a channel and supplier and renders browser OAuth or device-code instructions

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, httpx, PostgreSQL JSONB, Docker Compose, pytest, TypeScript, React 19, Next.js, Vitest, generated OpenAPI types

**Spec:** `docs/superpowers/specs/2026-09-04-account-pool-channels-and-suppliers-design.md`

## Global Constraints

- CLIProxyAPI is pinned to v7.2.146 and supplier behavior must match that version's real interfaces
- Supported CLIProxyAPI suppliers are `openai_codex`, `anthropic_claude`, `google_antigravity`, `kimi`, and `xai`
- `freebuff2api` remains a non-creatable placeholder with no image, source download, Docker runtime, credential contract, or gateway route
- Legacy JSONB payloads without new fields deserialize as `channel="cliproxyapi"` and `supplier="openai_codex"`
- Keep persisted `provider="openai"` and current OpenAI-compatible LiteLLM routing
- Keep Compose project/network `account-pool-<uuidhex>`, alias `cliproxy-<uuidhex>`, volume `account-pool-<uuidhex>-data`, service `cli-proxy-api`, and existing config/auth paths
- Do not migrate or rewrite historical rows, rebuild existing environments, or rename resources
- Remove the nonexistent `/v0/management/concurrency-limit` call; LiteLLM `max_parallel_requests` remains the environment concurrency authority
- Never hand-edit `ui/litellm-dashboard/src/lib/http/schema.d.ts`; regenerate it with `npm run gen:api`
- New source files begin with a concise Chinese responsibility description, and no unnecessary comments are added
- New Python code is fully typed, uses final variables and immutable values, and introduces no `Any`

---

### Task 1: Backward-Compatible Domain Contracts

**Files:**
- Modify: `account-pool/account_pool/domain.py`
- Modify: `account-pool/tests/account_pool/test_contracts.py`
- Modify: `account-pool/tests/test_account_pool.py`

**Interfaces:**
- Consumes: existing `EnvironmentRecord`, `EnvironmentView`, `CreateEnvironmentRequest`, `AuthorizationView`
- Produces: `ChannelKind`, `SupplierKind`, `AuthorizationFlow`; defaulted record/view/create fields; nullable authorization instructions

- [ ] **Step 1: Write failing legacy and public-contract tests**

Add tests that validate an exact current-format `EnvironmentRecord.model_dump(mode="json")` payload after removing `channel` and `supplier`, then assert:

```python
assert restored.channel is ChannelKind.CLIPROXYAPI
assert restored.supplier is SupplierKind.OPENAI_CODEX
```

Add a `to_view()` test asserting `channel` and `supplier` are present while `oauth_state`, `oauth_state_signature`, `oauth_provider_state`, `oauth_authorization_url`, `auth_file_name`, and `auth_index` are absent

Add request validation tests that accept all five supplier values for `cliproxyapi`, reject unknown enum strings, and prove `UpdateEnvironmentRequest.model_fields` contains neither `channel` nor `supplier`

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_contracts.py account-pool/tests/test_account_pool.py -q
```

Expected: failures because the new enums and fields do not exist

- [ ] **Step 3: Implement immutable domain values**

Add:

```python
class ChannelKind(StrEnum):
    CLIPROXYAPI = "cliproxyapi"
    FREEBUFF2API = "freebuff2api"


class SupplierKind(StrEnum):
    OPENAI_CODEX = "openai_codex"
    ANTHROPIC_CLAUDE = "anthropic_claude"
    GOOGLE_ANTIGRAVITY = "google_antigravity"
    KIMI = "kimi"
    XAI = "xai"


class AuthorizationFlow(StrEnum):
    BROWSER_OAUTH = "browser_oauth"
    DEVICE_CODE = "device_code"
```

Add defaulted `channel` and `supplier` fields to `EnvironmentRecord`, `EnvironmentView`, and `CreateEnvironmentRequest`, and copy them in `to_view()`

Extend `EnvironmentRecord` with defaulted authorization persistence fields:

```python
authorization_flow: AuthorizationFlow = AuthorizationFlow.BROWSER_OAUTH
authorization_user_code: str | None = None
```

Change `AuthorizationView` to:

```python
flow: AuthorizationFlow
authorization_url: HttpUrl
ssh_command: str | None
user_code: str | None
expires_at: datetime
```

- [ ] **Step 4: Run focused tests and confirm success**

Run the command from Step 2 and expect all selected tests to pass

- [ ] **Step 5: Commit**

```bash
git add account-pool/account_pool/domain.py account-pool/tests/account_pool/test_contracts.py account-pool/tests/test_account_pool.py
git commit -m "feat(account-pool): add channel and supplier contracts"
```

---

### Task 2: Static Supplier and Channel Registries

**Files:**
- Create: `account-pool/account_pool/channels/__init__.py`
- Create: `account-pool/account_pool/channels/base.py`
- Create: `account-pool/account_pool/channels/registry.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/__init__.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/__init__.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/base.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/registry.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/openai_codex.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/anthropic_claude.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/google_antigravity.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/kimi.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/suppliers/xai.py`
- Create: `account-pool/account_pool/channels/freebuff2api/__init__.py`
- Create: `account-pool/account_pool/channels/freebuff2api/placeholder.py`
- Test: `account-pool/tests/account_pool/test_channel_registry.py`

**Interfaces:**
- Consumes: `ChannelKind`, `SupplierKind`, `AuthorizationFlow`, `QuotaObservation`, `QuotaSnapshot`
- Produces: immutable `SupplierDefinition`, `SupplierRegistry`, `ChannelDefinition`, `ChannelRegistry`, `UnsupportedChannelError`

- [ ] **Step 1: Write failing supplier matrix tests**

Parametrize exact expectations:

```python
(
    SupplierKind.OPENAI_CODEX,
    AuthorizationFlow.BROWSER_OAUTH,
    "/v0/management/codex-auth-url",
    "codex",
    "codex",
    "codex",
    1455,
    "/auth/callback",
),
(
    SupplierKind.ANTHROPIC_CLAUDE,
    AuthorizationFlow.BROWSER_OAUTH,
    "/v0/management/anthropic-auth-url",
    "anthropic",
    "claude",
    "claude",
    54545,
    "/callback",
),
(
    SupplierKind.GOOGLE_ANTIGRAVITY,
    AuthorizationFlow.BROWSER_OAUTH,
    "/v0/management/antigravity-auth-url",
    "antigravity",
    "antigravity",
    "antigravity",
    51121,
    "/oauth-callback",
),
(
    SupplierKind.KIMI,
    AuthorizationFlow.DEVICE_CODE,
    "/v0/management/kimi-auth-url",
    "kimi",
    "kimi",
    "kimi",
    None,
    None,
),
(
    SupplierKind.XAI,
    AuthorizationFlow.DEVICE_CODE,
    "/v0/management/xai-auth-url",
    "xai",
    "xai",
    "xai",
    None,
    None,
),
```

Assert every lookup returns a frozen definition, `cliproxyapi` supports all five suppliers, and `freebuff2api` rejects every supplier with `UnsupportedChannelError("FreeBuff2API is not implemented")`

- [ ] **Step 2: Run the registry test and confirm failure**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_channel_registry.py -q
```

- [ ] **Step 3: Implement definitions and registries**

Define `SupplierDefinition` as a frozen slotted dataclass with exact typed fields:

```python
@dataclass(frozen=True, slots=True)
class SupplierDefinition:
    kind: SupplierKind
    authorization_flow: AuthorizationFlow
    authorization_path: str
    callback_provider_key: str
    auth_file_provider_key: str
    excluded_models_key: str
    callback_port: int | None
    callback_path: str | None
    quota_parser: Callable[[QuotaObservation], QuotaSnapshot]
```

Use Codex `parse_quota`; use a shared immutable empty parser returning `QuotaSnapshot(observed_at=observation.observed_at)` for suppliers without a verified structured quota contract

Build registries from `MappingProxyType` and fail closed on absent combinations. The FreeBuff placeholder must contain no image, command, endpoint, path, or runtime object

- [ ] **Step 4: Run focused tests and commit**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_channel_registry.py -q
git add account-pool/account_pool/channels account-pool/tests/account_pool/test_channel_registry.py
git commit -m "feat(account-pool): register channels and suppliers"
```

---

### Task 3: Supplier-Aware CLIProxyAPI Client

**Files:**
- Create: `account-pool/account_pool/channels/cliproxyapi/client.py`
- Modify: `account-pool/account_pool/cliproxy.py`
- Modify: `account-pool/account_pool/ports.py`
- Create: `account-pool/tests/account_pool/test_cliproxy_supplier_client.py`
- Modify: `account-pool/tests/test_account_pool.py`

**Interfaces:**
- Consumes: `SupplierDefinition`, `EnvironmentRecord`, `EnvironmentConfiguration`, `OAuthCallback`
- Produces: `AuthorizationStart`; `CLIProxyAPIClient` methods accepting the selected supplier definition

- [ ] **Step 1: Write failing management-client tests**

Define an immutable result:

```python
@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    authorization_url: str
    provider_state: str
    user_code: str | None
    expires_in_seconds: int | None
```

Use `httpx.MockTransport` to assert all five `start_authorization()` calls target their exact registered paths and parse browser and device responses. For Kimi/xAI, assert `user_code` and `expires_in` are retained

Parametrize callback tests for Codex, Claude, and Antigravity, asserting POST `/v0/management/oauth-callback` uses callback provider keys `codex`, `anthropic`, and `antigravity`

Return multiple auth files and assert `read_account()` selects only the selected supplier's `provider` or `type`. Assert the model lookup uses the selected file name and the exclusion payload uses exactly that supplier's key

Record every request and assert `/v0/management/concurrency-limit` never occurs during `apply_configuration()`

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_cliproxy_supplier_client.py -q
```

- [ ] **Step 3: Implement the supplier-aware client**

Move the existing typed HTTP response models and transport into `channels/cliproxyapi/client.py`. Expose:

```python
async def start_authorization(
    self,
    record: EnvironmentRecord,
    supplier: SupplierDefinition,
) -> AuthorizationStart

async def authorization_status(self, record: EnvironmentRecord, state: str) -> str

async def submit_callback(
    self,
    record: EnvironmentRecord,
    supplier: SupplierDefinition,
    callback: OAuthCallback,
) -> None

async def read_account(
    self,
    record: EnvironmentRecord,
    supplier: SupplierDefinition,
) -> EnvironmentRecord

async def data_plane_health_check(self, record: EnvironmentRecord) -> bool

async def apply_configuration(
    self,
    record: EnvironmentRecord,
    supplier: SupplierDefinition,
    configuration: EnvironmentConfiguration,
) -> None
```

`apply_configuration()` performs only real operations: proxy URL, supplier-specific excluded models, and credential enabled state. It does not call a CLIProxy concurrency endpoint

Keep `account_pool.cliproxy` as compatibility re-exports until all call sites and tests are migrated, avoiding a broad unrelated import break

- [ ] **Step 4: Run old and new client tests**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_cliproxy_supplier_client.py account-pool/tests/account_pool/test_quota.py account-pool/tests/test_account_pool.py -q
```

- [ ] **Step 5: Commit**

```bash
git add account-pool/account_pool/channels/cliproxyapi/client.py account-pool/account_pool/cliproxy.py account-pool/account_pool/ports.py account-pool/tests
git commit -m "feat(account-pool): adapt CLIProxy management by supplier"
```

---

### Task 4: Channel Runtime and Stable Docker Identity

**Files:**
- Create: `account-pool/account_pool/channels/cliproxyapi/runtime.py`
- Create: `account-pool/account_pool/channels/cliproxyapi/channel.py`
- Modify: `account-pool/account_pool/compose_renderer.py`
- Modify: `account-pool/account_pool/compose_runtime.py`
- Modify: `account-pool/account_pool/compose.py`
- Modify: `account-pool/tests/account_pool/test_compose_renderer.py`
- Modify: `account-pool/tests/test_account_pool.py`

**Interfaces:**
- Consumes: existing low-level Docker operations, settings, secret deriver, persisted channel/supplier
- Produces: `CLIProxyAPIChannel`; channel-dispatched provision, restore, start/stop, cleanup, client actions, and gateway projection

- [ ] **Step 1: Strengthen failing compatibility tests**

Build a legacy JSON payload with no channel/supplier, render it, and assert exact current values:

```python
assert compose["name"] == f"account-pool-{record.id.hex}"
assert compose["services"].keys() == {"cli-proxy-api"}
assert compose["services"]["cli-proxy-api"]["networks"]["environment"]["aliases"] == [
    f"cliproxy-{record.id.hex}"
]
assert compose["volumes"] == {
    "cliproxy-data": {"name": f"account-pool-{record.id.hex}-data"}
}
assert compose["services"]["cli-proxy-api"]["command"] == [
    "./CLIProxyAPI",
    "-config",
    "/data/config/config.yaml",
]
assert "ports" not in compose["services"]["cli-proxy-api"]
```

Add tests that a FreeBuff placeholder cannot return a renderer/runtime spec and deletion/restore resolve using the persisted channel

- [ ] **Step 2: Run focused tests and confirm new tests fail**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_compose_renderer.py account-pool/tests/test_account_pool.py -q
```

- [ ] **Step 3: Implement channel ownership without renaming resources**

Create `CLIProxyAPIChannel` as the composition root for `ComposeRuntime`, `CLIProxyAPIClient`, and `SupplierRegistry`. Keep Docker command execution in `ComposeRuntime`; move only CLIProxy-specific rendering/seed selection behind the channel boundary

The channel's gateway projection returns the same base URL and derived key plus an explicit `custom_llm_provider="openai"`

Do not implement a FreeBuff runtime. Its registry entry must fail before reaching `ComposeRuntime`

- [ ] **Step 4: Run focused tests and commit**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_compose_renderer.py account-pool/tests/test_account_pool.py -q
git add account-pool/account_pool/channels/cliproxyapi account-pool/account_pool/compose_renderer.py account-pool/account_pool/compose_runtime.py account-pool/account_pool/compose.py account-pool/tests
git commit -m "refactor(account-pool): dispatch runtime through channels"
```

---

### Task 5: Channel-Aware Lifecycle and Authorization

**Files:**
- Modify: `account-pool/account_pool/service.py`
- Modify: `account-pool/account_pool/app.py`
- Modify: `account-pool/account_pool/api.py`
- Modify: `account-pool/account_pool/ports.py`
- Create: `account-pool/tests/account_pool/test_legacy_record_compatibility.py`
- Modify: `account-pool/tests/test_account_pool.py`

**Interfaces:**
- Consumes: `ChannelRegistry`, `CLIProxyAPIChannel`, `SupplierDefinition`, `AuthorizationStart`
- Produces: creation validation before persistence, provider-specific browser callbacks, device-code authorization, and channel-dispatched lifecycle

- [ ] **Step 1: Write failing lifecycle tests**

Add one create test per CLIProxy supplier asserting the requested identities are saved and the exact supplier adapter is used

Add FreeBuff tests asserting the result is `FailureCode.INVALID`, repository remains empty, and fake runtime/client call lists remain empty

Parametrize browser authorization assertions:

```python
(SupplierKind.OPENAI_CODEX, 1455, "/auth/callback")
(SupplierKind.ANTHROPIC_CLAUDE, 54545, "/callback")
(SupplierKind.GOOGLE_ANTIGRAVITY, 51121, "/oauth-callback")
```

Assert SSH uses `-L <local-port>:127.0.0.1:<manager-port>`, the response flow is `browser_oauth`, and `user_code is None`

For Kimi and xAI, assert response flow is `device_code`, `ssh_command is None`, the returned user code is persisted, no callback submission occurs, and polling status `ok` enters validation

Add idempotency tests proving an unexpired authorization operation returns its persisted URL, flow, user code, and SSH instruction

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests/test_account_pool.py account-pool/tests/account_pool/test_legacy_record_compatibility.py -q
```

- [ ] **Step 3: Refactor service dispatch**

Inject one `ChannelRegistry` into `EnvironmentService`. Validate the channel/supplier combination before the initial `repository.save()` call. Persist requested identities and authorization flow

Replace direct `_runtime` and `_cli_proxy` calls with the channel selected from each record. The service retains locks, state signatures, state consumption, version checks, retries, cooldown state, and persistence

Use the supplier's callback port in `_authorization_view()`. For device flows, derive expiry from `expires_in_seconds` with an upper bound matching the returned value and omit SSH

Add `/callback` and `/oauth-callback` routes to the same private callback handler used by `/auth/callback`; all three paths must call the unchanged signed-state validation before supplier callback submission

- [ ] **Step 4: Wire app startup and shutdown**

Construct the CLIProxy client, runtime, supplier registry, CLIProxy channel, FreeBuff placeholder, and channel registry in `create_app()`. Restore control-plane connections only for supported persisted channels. Close the single shared HTTP client during shutdown

- [ ] **Step 5: Run manager tests and commit**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests -q
git add account-pool/account_pool account-pool/tests
git commit -m "feat(account-pool): route lifecycle by channel and supplier"
```

---

### Task 6: LiteLLM Boundary and Protocol-Explicit Reconciliation

**Files:**
- Modify: `litellm/proxy/management_endpoints/account_pool_endpoints.py`
- Modify: `litellm/proxy/management_endpoints/account_pool_reconciler.py`
- Modify: `tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py`
- Modify: `tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py`

**Interfaces:**
- Consumes: Manager public environment/authorization and gateway JSON
- Produces: strict LiteLLM proxy models with channel/supplier/authorization flow and protocol-explicit gateway deployment values

- [ ] **Step 1: Write failing endpoint contract tests**

Update manager fixtures with:

```python
"channel": "cliproxyapi",
"supplier": "openai_codex",
"configuration_pending": False,
```

Add create forwarding tests asserting a request containing `channel="cliproxyapi"` and `supplier="anthropic_claude"` reaches the Manager unchanged. Add validation tests rejecting unknown values and a malformed Manager response

Update authorization fixtures and assertions for `flow`, nullable `ssh_command`, and nullable `user_code`

- [ ] **Step 2: Write failing reconciler compatibility tests**

Add `custom_llm_provider: Literal["openai"]` to expected gateway input. Assert the generated deployment still contains:

```python
assert deployment.provider_model == f"openai/{model}"
assert deployment.litellm_params["custom_llm_provider"] == "openai"
assert deployment.id == str(
    uuid5(NAMESPACE_URL, f"litellm-account-pool:{environment.id.hex}:{model}")
)
```

Assert non-routable snapshots produce no deployments

- [ ] **Step 3: Run focused tests and confirm failure**

```bash
.venv/Scripts/python.exe -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py -q
```

- [ ] **Step 4: Implement strict contracts and gateway protocol field**

Add exact `Literal` types for both channels and all five suppliers to create and environment models. Include `configuration_pending`. Update authorization types

Add `custom_llm_provider` to both Manager and LiteLLM gateway models. Store it on `ManagedDeployment` and use it in `litellm_params`; retain `provider_model=f"openai/{model}"`, deployment IDs, and model metadata unchanged

- [ ] **Step 5: Run focused tests and commit**

```bash
.venv/Scripts/python.exe -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py -q
git add litellm/proxy/management_endpoints/account_pool_endpoints.py litellm/proxy/management_endpoints/account_pool_reconciler.py tests/test_litellm/proxy/management_endpoints
git commit -m "feat(proxy): expose account pool channel contracts"
```

---

### Task 7: Generated Dashboard Types and Request Layer

**Files:**
- Generate: `ui/litellm-dashboard/src/lib/http/schema.d.ts`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolTypes.ts`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolApi.ts`
- Create: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolApi.test.ts`

**Interfaces:**
- Consumes: updated LiteLLM OpenAPI models
- Produces: `AccountPoolChannel`, `AccountPoolSupplier`, `AccountPoolCreateRequest`; create API carrying selected values

- [ ] **Step 1: Regenerate OpenAPI types**

```bash
cd ui/litellm-dashboard && npm run gen:api
```

Verify generated `AccountPoolEnvironment`, `AccountPoolCreateRequest`, and `AccountPoolAuthorization` contain the exact new fields. Do not manually alter the generated file

- [ ] **Step 2: Write a failing API payload test**

Mock the network boundary, call:

```typescript
createAccountPoolEnvironment(accessToken, {
  name: "Claude account",
  channel: "cliproxyapi",
  supplier: "anthropic_claude",
});
```

Assert the request body is exactly the supplied object and contains no image, command, callback, internal URL, or secret fields

- [ ] **Step 3: Implement local aliases and create request**

Export:

```typescript
export type AccountPoolChannel = AccountPoolEnvironment["channel"];
export type AccountPoolSupplier = AccountPoolEnvironment["supplier"];
export type AccountPoolCreateRequest = components["schemas"]["AccountPoolCreateRequest"];
```

Change the API function to accept the generated create request instead of hard-coding OpenAI

- [ ] **Step 4: Run focused test and commit**

```bash
cd ui/litellm-dashboard && npm run test:unit -- AccountPoolApi.test.ts
cd ../.. && git add ui/litellm-dashboard/src/lib/http/schema.d.ts 'ui/litellm-dashboard/src/app/(dashboard)/account-pool'
git commit -m "feat(dashboard): use account pool channel API types"
```

---

### Task 8: Dashboard Channel Selection and Authorization Guidance

**Files:**
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCreateDialog.tsx`
- Modify: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.tsx`
- Create: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCreateDialog.test.tsx`
- Create: `ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.test.tsx`
- Modify: `ui/litellm-dashboard/src/locales/en.json`
- Modify: `ui/litellm-dashboard/src/locales/zh-CN.json`

**Interfaces:**
- Consumes: generated channel, supplier, and authorization flow types
- Produces: accessible selectors, disabled FreeBuff placeholder, browser OAuth instructions, device-code instructions, and accurate card identity

- [ ] **Step 1: Write failing component tests**

Test that the initial form selects CLIProxyAPI and OpenAI Codex, exposes five CLIProxyAPI supplier options, and passes selected values to the API

Test that FreeBuff2API is visible with “暂未实现”/“Not implemented”, cannot be submitted, and causes no create API call

For a browser OAuth result, assert the SSH command and authorization link are visible and no device-code field exists

For a device-code result, assert the authorization link and copyable user code are visible and no SSH field exists

For a Claude environment card, assert the UI shows translated CLIProxyAPI and Anthropic Claude labels instead of a static OpenAI label

- [ ] **Step 2: Run focused component tests and confirm failure**

```bash
cd ui/litellm-dashboard && npm run test:component -- AccountPoolCreateDialog.test.tsx AccountPoolCard.test.tsx
```

- [ ] **Step 3: Implement selectors and flow-specific instructions**

Use the dashboard's existing `Select` components. Keep the selectable supplier list as a typed immutable tuple. FreeBuff appears as a disabled option and selecting channels always resets supplier to a valid value

Render SSH only for `flow === "browser_oauth"` and a non-null command. Render user code only for `flow === "device_code"` and a non-null code

Replace the static provider card line with translated channel and supplier labels

Add plain Chinese and English translation keys for channel names, supplier names, authorization modes, device code, and unavailable placeholder text

- [ ] **Step 4: Run focused tests and lint touched UI**

```bash
cd ui/litellm-dashboard && npm run test:component -- AccountPoolCreateDialog.test.tsx AccountPoolCard.test.tsx
npx eslint 'src/app/(dashboard)/account-pool/**/*.{ts,tsx}'
```

- [ ] **Step 5: Commit**

```bash
cd ../.. && git add 'ui/litellm-dashboard/src/app/(dashboard)/account-pool' ui/litellm-dashboard/src/locales/en.json ui/litellm-dashboard/src/locales/zh-CN.json
git commit -m "feat(dashboard): select account pool suppliers"
```

---

### Task 9: Documentation and End-to-End Regression Verification

**Files:**
- Modify: `account-pool/README.md`
- Modify: `account-pool/PROJECT_STRUCTURE.md`
- Modify if generated by budget fixes: `ruff-strict-budget.json`, `type-discipline-budget.json`, `basedpyright-code-budget.json`

**Interfaces:**
- Consumes: completed manager, LiteLLM, and dashboard behavior
- Produces: accurate operator/developer documentation and verified release state

- [ ] **Step 1: Update architecture documentation**

Document the channel/supplier distinction, five CLIProxyAPI suppliers and their authorization modes, three browser callback ports/paths, two device-code flows, legacy defaults, stable Docker identities, OpenAI-compatible gateway routing, and FreeBuff2API's no-resource placeholder boundary

Update the project tree to show `channels/cliproxyapi/suppliers/` and `channels/freebuff2api/`. Remove statements saying the product only supports OpenAI Codex or that CLIProxyAPI has a runtime concurrency endpoint

- [ ] **Step 2: Run the complete account-pool suite**

```bash
account-pool/.venv/Scripts/python.exe -m pytest account-pool/tests -q
```

Expected: all account-pool tests pass

- [ ] **Step 3: Run focused LiteLLM regression tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py tests/local_testing/test_router_max_parallel_requests.py -q
```

Expected: all selected tests pass and UUID-based environment concurrency remains intact

- [ ] **Step 4: Run focused dashboard tests, API sync, and build checks**

```bash
cd ui/litellm-dashboard && npm run gen:api
npm run test:unit -- AccountPoolApi.test.ts
npm run test:component -- AccountPoolCreateDialog.test.tsx AccountPoolCard.test.tsx
npx eslint 'src/app/(dashboard)/account-pool/**/*.{ts,tsx}'
npm run build
```

Expected: generated API types remain unchanged after regeneration, tests pass, lint reports no touched-file errors, and Next.js builds

- [ ] **Step 5: Run repository checks permitted by this Windows environment**

```bash
git diff --check
git status --short
```

Attempt `make check` only if GNU Make and `python3` are now present. If unavailable, record that environment limitation rather than claiming the check passed. If a budget gate is lowered by the changes, run `make lint-budget-update` before the final commit

- [ ] **Step 6: Review final invariants**

Confirm from tests and diff that legacy records require no migration, no Docker identity changed, no call targets `/v0/management/concurrency-limit`, FreeBuff creates no side effect, every supplier uses its fixed real endpoint/key/callback contract, secrets remain absent from public models, and generated UI types were not hand-edited

- [ ] **Step 7: Commit documentation and final generated updates**

```bash
git add account-pool/README.md account-pool/PROJECT_STRUCTURE.md ui/litellm-dashboard/src/lib/http/schema.d.ts
git commit -m "docs(account-pool): document channel supplier support"
```
