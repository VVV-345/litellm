# Account Pool Desired-State Management and LiteLLM Sync Plan

## Goal

Complete the next Phase 1 delivery after the persistent catalog foundation:

- PostgreSQL records every channel mutation before an external LiteLLM call
- catalog tables expose only applied state to the scheduler
- create, update, detach, managed delete, external delete, import, and retry have explicit semantics
- every external action has a stable operation ID and idempotency key
- a reconciler can finish or report interrupted operations without persisting an API key
- `/api/channels` and the temporary `/api/accounts` aliases call one application service

Scheduler startup cutover, Redis projection, audit actor envelopes, and Dashboard work follow after this delivery proves management consistency.

## Invariants

1. A real provider key may exist only in the inbound request and the same process's immediate LiteLLM call. It never enters PostgreSQL, Redis, YAML, JSON, logs, errors, operation payloads, API responses, browser storage, tests, or commits.
2. The current catalog tables remain applied state. A failed desired operation cannot change scheduler-visible configuration.
3. The desired payload is a versioned Pydantic model serialized as JSONB. Unknown fields are rejected when writing and reading; it is not an arbitrary dictionary.
4. `operation_id`, `binding_id`, and a pool-managed deployment ID are allocated before the first LiteLLM call and remain stable across retries.
5. `idempotency_key` is unique. Repeating the same key and payload returns the existing operation; repeating it with different business content returns a structured conflict.
6. LiteLLM `model_info` contains only `channel_id`, `binding_id`, `operation_id`, and `managed_by=account_pool`.
7. Externally managed deployments are never deleted by channel deletion. Their deletion requires the separate confirmed binding operation.
8. Connectivity, decoding, and database failures are not converted into business conflicts.

## State Model

`LiteLLM_AccountPoolSyncOperation` stores:

- `operation_id`
- `idempotency_key`
- `channel_id`
- `action`: `create_channel | update_channel | detach_channel | delete_channel | import_channel | delete_external_deployment`
- `status`: `pending_create | pending_update | pending_delete | applied | failed`
- `delete_mode`: null, `detach_only`, or `delete_managed_deployment`
- `desired_schema_version`
- `desired_payload` JSONB validated as `ChannelDesiredState`
- `attempt_count`
- `requires_key`
- safe error code and safe error message
- created, updated, and applied timestamps

The existing channel, binding, and policy tables remain the scheduler-visible applied catalog. Applying an operation updates those tables and the operation status in one PostgreSQL transaction.

## Operation Flow

### Create

1. Validate the request and build a desired channel with stable IDs.
2. Persist `pending_create` before contacting LiteLLM.
3. Create each pool-managed deployment with its preallocated ID and Account Pool markers.
4. On complete success, atomically replace the channel's applied catalog and mark the operation `applied`.
5. On a safe upstream failure, mark `failed`; `requires_key=true` only when retrying the missing credential-bearing call needs a newly submitted key.
6. The reconciler scans marked deployments. It can complete a crash-after-create operation without recovering the provider key.

### Update

1. Persist the complete desired channel as `pending_update`.
2. Create new pool-managed bindings, update retained pool-managed bindings, and leave external bindings untouched unless explicitly detached.
3. Apply the catalog only after all required LiteLLM calls succeed.
4. Cleanup of removed managed deployments is retryable and must not make external bindings disappear silently.

### Detach and Delete

- `detach_channel` and `delete_channel` persist `pending_delete`, making the channel unavailable to new management operations while existing applied state remains visible until runtime cutover adds pending-delete eligibility filtering.
- `detach_only` removes Account Pool bindings and preserves every LiteLLM deployment.
- `delete_managed_deployment` deletes only pool-managed deployments, detaches external bindings, then removes the applied channel.
- `delete_external_deployment` targets one externally managed binding and requires an explicit confirmation value in the command.

## Tasks

### Task 1: Typed operation domain

Create immutable enums and Pydantic models for desired channels, desired bindings, operation identity, action, status, delete mode, safe failure, and result. Tests must reject naive timestamps, secret-shaped fields, invalid action/status combinations, and external deletion without confirmation.

Commit: `feat(account-pool): define channel sync operations`

### Task 2: Prisma schema and official migration

Add `LiteLLM_AccountPoolSyncOperation` to all three byte-identical Prisma schemas. Generate the migration with the official migration script without freshness or destructive bypasses. Append only Prisma-inexpressible checks for action, status, delete mode, schema version, and attempt count.

Commit: `feat(account-pool): persist channel sync operations`

### Task 3: PostgreSQL operation repository

Add a protocol and psycopg implementation for:

- idempotent operation creation
- operation loading
- pending/failed operation listing
- attempt recording
- safe failure recording
- atomic catalog apply

Integration tests execute the committed migration in an isolated PostgreSQL schema and cover same-key concurrency, payload conflicts, failed operations leaving catalog unchanged, and atomic apply.

Commit: `feat(account-pool): add sync operation repository`

### Task 4: LiteLLM synchronization client

Refactor the admin client behind a typed protocol. Create and update calls accept preallocated IDs and markers. Add typed listing of Account Pool managed deployments for reconciliation. Tests validate request bodies and prove provider keys do not appear in returned values, safe failures, or markers.

Commit: `feat(account-pool): add idempotent LiteLLM sync client`

### Task 5: Desired-state channel service

Implement the application service for create, update, import, detach, channel delete, external binding delete, and retry. Persist desired state before the first fake-admin call. Use dependency injection and test call order, ownership rules, idempotency, rollback-free failure recording, and secret boundaries.

Commit: `feat(account-pool): add desired-state channel management`

### Task 6: Reconciler

Implement one bounded reconciliation pass and a separately scheduled loop. Reconcile marked LiteLLM deployments against pending operations, finish crash-after-create operations, retry non-secret operations, report orphans, and leave credential-required operations failed until an administrator resubmits a key.

Commit: `feat(account-pool): reconcile LiteLLM channel state`

### Task 7: Channel API and compatibility aliases

Add the Phase 1 channel endpoints. Require `Idempotency-Key` for mutations and explicit delete semantics. Point `/api/accounts` CRUD aliases at the same service and add deprecation headers. Do not keep parallel YAML mutation logic.

Commit: `feat(account-pool): expose persistent channel management`

## Verification

Run at minimum:

```powershell
uv run --project account-pool --extra test pytest account-pool/tests/sync -q
uv run --project account-pool --extra test pytest account-pool/tests -q
uv run ruff check account-pool/account_pool account-pool/tests
uv run basedpyright account-pool/account_pool account-pool/tests
uv lock --project account-pool --check
uv run prisma validate --schema schema.prisma
git diff --check
```

With an isolated PostgreSQL database, execute all integration and concurrency tests against committed migrations. Do not claim the full root build or Dashboard verification in this delivery.

## Rollback

Code rollback leaves the additive sync-operation table unused. Applied catalog rows retain their previous meaning. No down migration is generated and no destructive schema action is required.

## Deferred Phase 1 Work

After this delivery:

1. wire application startup and scheduler projection to PostgreSQL
2. build/version Redis configuration cache from PostgreSQL
3. add signed actor envelopes and append-only management audit events
4. add LiteLLM server-side proxy endpoints with RBAC and CSRF enforcement
5. add the LiteLLM Dashboard channel page using existing provider/model controls
