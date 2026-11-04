# Verified access-token fallback after refresh failure

## Approved scope

The user explicitly authorized implementing the previously discussed design and pushing verified related commits to the existing CLIProxyAPI分支 remote branch. This is not production deployment, credential mutation, reset-credit use, or live verification authorization

This is a cross-layer change. Reuse the existing policy sync, CPA per-auth coordinator and cooldown persistence, controlled inference verifier, Manager routing/lease/budget gates, LiteLLM billing, and card controls. Do not introduce another token refresher or a global unauthorized bypass

## Contract

- Per-card CodexPolicy.allow_valid_access_after_refresh_failure defaults to false. Sync it as auth metadata codex_allow_valid_access_after_refresh_failure; verify runtime capability before enabling
- CPA stores refresh-failure origin and credential binding separately from access rejection and model errors. Controlled proof is bound to current credential, proxy and selected model, with a conservative access expiry deadline
- CPA capability GET /v0/management/codex/access-token-fallback returns supported=true, version=1, token_refresh=false. POST accepts name, auth_index, model, token_fingerprint and verifies a complete OK response without OAuth, retry or fallback. It never redeems credits or clears independent limits
- CPA auth-file status includes access_token_fallback with enabled, active, reason, expires_at, verified_models. No raw credentials or credential fingerprints are exposed in the UI
- Manager POST /api/environments/{id}/access-token-fallback/verify accepts model and confirmed=true. It requires current enabled model, no independent management blocks, current identity/proxy/config, and the normal quota/routing prerequisites. Its response reflects verified versus actually usable state separately
- UI has a default-off setting, explicit verify action, temporary-availability warning, estimated expiry, and existing reauthorization action. Normal calls remain on the ordinary accounting path

## Work and acceptance

1. CPA error-origin/state persistence and verifier, selector and request paths, proof invalidation and no implicit rejected refresh. TDD for disabled default, proof success, tool/interrupted streams, expiry/rejection, transient/model failures, independent limits, credential/proxy change, disable and restart
2. Manager policy/capability, persistence/views and normal routing plus API proxy. Test complete local mock request and usage accounting, no bypass of quotas, budget or concurrency
3. UI setting/status/verify action and reauthorization, explicit-path component tests, source/test types, isolated production build
4. Package adapter SHA, clean apply/build and management auth, transaction baseline/modified/rollback checks, separate commits, scoped remote push only after necessary gates pass

Production acceptance remains unperformed. JWT exp is an unverified claimed bound, never proof of access validity. No promise of permanent renewal

## Approved credential lifecycle and card UI extension

Use one per-card credential-refresh action, separate from access-only verification and reauthorization. The existing CPA refresh coordinator owns refresh, pending-save retry, and lifecycle generations. Persist only fields actually returned by the provider; retain omitted refresh and ID tokens. Expose redacted access expiry, real scheduler next-at/backoff/pause state and last refresh outcome

Deletion must remove only the card-owned credentials, runtime pending state and configuration through the existing ownership and cleanup steps. Concurrent or delayed refresh, import and authorization results must not recreate deleted cards. Cleanup failures remain visible and retryable; audit and backup retention is unchanged. This authorizes implementing deletion, not deleting live cards

Keep desktop cards at two columns, small screens at one. Show name, state, quota/reset, credential warning and primary actions. Move identifiers and diagnostics into details. Use existing UI primitives, generated API types and query invalidation without new dependencies

Acceptance extends to synthetic-provider rotation/omitted fields/save failures/concurrency, delete-versus-refresh and second-card isolation, real scheduler metadata, component/type/build checks and browser visual/keyboard checks. Root integrates the independently owned CPA, Manager lifecycle and UI changes, then commits and pushes only validated related work. No deployment or live credential operations
