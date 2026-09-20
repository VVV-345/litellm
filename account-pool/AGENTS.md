# Account Pool Development Instructions

Read `../CLAUDE.md` before making changes in this directory

Read [MAINTENANCE.md](MAINTENANCE.md) when moving modules, extracting shared code, removing exports, or changing the LiteLLM integration. It contains the directory map, migration procedure, verification commands, and dated audit findings

## Product Scope

This module manages isolated CLIProxyAPI environments from the LiteLLM dashboard. The first supported upstream is OpenAI. Each environment is provisioned with Docker Compose, publishes no host ports, and is reachable only through the approved gateway path

An environment is complete only after the user finishes authorization, the required credential and runtime configuration is stored securely, model discovery succeeds, and the environment reaches the ready state

## Architecture Boundaries

- Keep the control plane separate from the request data plane
- Keep UI, API transport, application orchestration, domain state, Docker operations, credential storage, quota collection, and gateway routing in separate modules
- LiteLLM owns authentication, authorization, metadata, configuration APIs, and dashboard integration
- A narrowly scoped environment manager owns Compose lifecycle operations. Do not expose the Docker socket to the public LiteLLM process
- Use only the configured, strictly validated TCP Docker Socket Proxy endpoint. The Socket Proxy must stay on a Manager-only internal network, separate from LiteLLM, and remains a control-plane reduction rather than a complete boundary against malicious Compose requests
- CLIProxyAPI stays inside each isolated environment and must not publish host ports
- Each account network must retain expected upstream egress. Do not set it to `internal`; keep it separate from the internal shared control network
- The gateway enforces environment availability, enabled models, concurrency, cooldown, and routing before forwarding requests
- Model discovery and quota parsing are provider adapters. Do not mix provider-specific parsing with environment lifecycle logic
- Treat request ingress routing and outbound proxy selection as different concepts and configuration fields

## Engineering Rules

- Every new source file must begin with a concise Chinese file-level description of its responsibility and boundary, using the native comment or module-documentation syntax of that language
- Add Chinese comments for non-obvious security, concurrency, lifecycle, state-machine, and failure-recovery logic. Do not narrate obvious statements line by line
- Prefer small typed functions, explicit inputs and outputs, dependency injection, and composition
- Reuse existing LiteLLM dashboard components, API clients, validation helpers, permission checks, and status patterns before adding account-pool-specific equivalents
- Extract shared UI or business logic when it has multiple real call sites or clearly removes domain duplication. Do not create speculative abstractions
- Keep provider adapters, quota-window calculations, cooldown decisions, and gateway eligibility checks independently testable
- Model lifecycle failures as explicit states and results. Provisioning, authorization, recovery, deletion, and retries must be idempotent
- Never build shell commands from user input. Use strict identifiers, approved Compose templates, and structured Docker APIs
- Never log or return access tokens, refresh tokens, session cookies, proxy credentials, or complete generated configuration files

## Refactoring Boundaries

- Extract pure calculations into `application/` and provider quota parsing into `providers/usage/`; keep CLIProxyAPI protocol and transport code under `channels/cliproxyapi/`. Add a shared helper only when existing callers need the same semantics
- Keep transactions, locks, idempotency keys, compensation, and network call order with their current orchestration owner during a structural refactor. Parsing helpers must not import service orchestration or construct network clients
- Preserve supported compatibility imports such as `account_pool.provider_quota`. Verify configuration references, package contents, dynamic loading, and cross-service callers before deleting an apparently unused entrypoint
- Keep API paths, status codes, response fields, default values, stable account/model identifiers, and gateway authorization scope unchanged during module moves. A test failure caused by a changed default requires an explicit behavior decision, not an automatic expectation update
- Reuse LiteLLM authentication, routing, pricing, and logging mechanisms. Preserve card restrictions, provider-specific quota semantics, proxy selection, and the boundary that prevents replay after stream output or unsafe tool execution

## Environment Lifecycle

Use an explicit lifecycle such as `provisioning`, `awaiting_authorization`, `validating`, `ready`, `cooling_down`, `disabled`, `error`, and `deleting`. Manual disablement has higher priority than automatic recovery. An elapsed cooldown does not become ready until a health check succeeds

Create each environment under a generated immutable identifier. The display name is editable but must never be used as a filesystem path, Compose project name, container name, or routing identity

## UI Requirements

- Add a Chinese `号池` entry to the existing LiteLLM sidebar and permission system
- Reuse the dashboard design system and shared cards, dialogs, forms, badges, switches, model selectors, pagination, and error states
- Cards show name, lifecycle and availability state, remaining quota, reset time, enabled state, and enabled models
- Provide an explicit configuration action for accessibility and mobile use. Double-click may remain only as a desktop shortcut
- Environment settings support display-name changes, concurrency, quota and cooldown details, manual cooldown control, outbound proxy selection, and enabled-model selection
- Every mutable form requires explicit save and clear success or failure feedback

## Verification

- Add focused tests for state transitions, authorization completion, Docker request validation, concurrency enforcement, cooldown recovery, provider parsing, model enablement, and secret redaction
- Verify that generated Compose services publish no ports and cannot reach another environment directly
- Verify authorization expiry, partial provisioning failure, restart recovery, duplicate requests, deletion retries, quota exhaustion, reset recovery, and unavailable proxy profiles
- Run Manager tests from the repository root with an environment that can import both Manager and LiteLLM. Changes crossing the service boundary also require the corresponding `tests/test_litellm/proxy/management_endpoints/test_account_pool_*.py` checks
- Diagnose test timeouts before changing production retry settings. Legacy card-key tests can perform a real 60-second cooldown; test setup can also fetch the remote model cost map. These are distinct from virtual-key routing and require separate evidence
- Check package imports and wheel contents after moves. Keep historical test counts and outstanding findings in the dated playbook, rather than treating them as permanent guarantees
