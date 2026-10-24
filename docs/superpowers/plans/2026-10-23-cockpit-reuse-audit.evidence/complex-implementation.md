# D2.2 bounded native subject implementation

Current: native static subject reader / focused GREEN and strict types passed / frozen for controller review and transaction verification

Target workspace is C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm at base d06bd940ec. Original D:/Work2api/litellm was only used for its existing Python environment, not modified. No commit, external service call, real database access, build, credential operation, deployment or subagent dispatch was performed

## Changed files

- C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_cockpit_subject.py
- C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/tests/test_litellm/proxy/management_endpoints/test_account_pool_cockpit_subject.py
- C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py

Reader and endpoint test retain original LF; subject test retains original CRLF. Controller owns schema, documentation and four-role artifacts. No production endpoint edit was needed because its report already preserves the subject object

## Supported bounded behavior

The result now requires an explicit model_scope for verified subjects. Its shape is {kind: unrestricted, models: []} or {kind: restricted, models: [finite literal labels]}. Null means unresolved; restricted empty scopes and verified results without model scope are invalid. Reader sorts and deduplicates the finite intersection. These labels are not canonical upstream IDs, provider mappings or a declaration of runtime request permission

Key-only, personal-user, team, standalone-project and matching user/team/project chains can pass when their current writer rows and all checked authorization facts are supported. Native common_checks checks user models only for personal keys without a team; team keys use team models, not the personal user's models. Key, applicable user/team and project finite lists intersect, with empty native lists imposing no extra model restriction. A disjoint intersection stays model_scope_unverified with no card, never unrestricted

Each referenced owner must be present and match its requested ID. Project.team_id must equal the key's team_id, including null equality. Any non-null owner parent team must also match. User+team keys require a current matching LiteLLM_TeamMembership with null budget_id. Missing membership is deliberately more conservative than current runtime auth, which can treat absent membership as no extra restriction

The live query is one parameterized, LIMIT 1 writer statement joining only referenced user/team/project/membership rows, with the tombstone NOT EXISTS in the same statement. Preliminary tombstone checking remains. Narrow owner projections omit password, email, raw metadata and all unrelated owner records. Scalar/JSON authorization conditions are projected as permissions_unrestricted, validated as strict Boolean; null, malformed facts, mismatched IDs and blocked owner facts fail closed

## Unsupported boundary

All model sentinels (all-team-models, all-proxy-models, no-default-models), wildcard/pattern strings, empty or whitespace-bearing labels remain unresolved, including sentinel combinations. Owner models are validated even when personal model limits would not apply to a team key, a deliberate conservative restriction

Key aliases, config, permissions, router settings, access groups, policies, route/cache controls, non-default key types, model budgets/fallbacks, budget limits, organization/agent/object-permission/budget references, non-null max_budget/budget_duration/budget_reset_at and extra metadata remain unsupported. Existing max_budget=0 and budget timestamp/duration fields were previously ignored; new RED cases demonstrate they now block rather than silently verify

Owner projections reject nonempty metadata and model budgets, organization/object-permission references, configured user/team budgets and rate/concurrency limits, user cache controls/policies, team router settings/access groups/policies/default member model policies/member permissions/team aliases/guardrail configuration, and project budget/object-permission/rate-limit settings. Membership budget references remain unsupported without reading those budget rows

Blocked, expired, missing, revoked, malformed, legacy-card and unbound native keys preserve their original failure behavior. Candidate scope cannot expand the single card. Manager request stays card-only; public report retains explicit subject.model_scope, ready_to_migrate=false and native_runtime_authorization_unverified. No model policy is published to Manager or data plane in this turn

## Auth semantics inspected

litellm/proxy/auth/auth_checks.py: common_checks, _run_project_checks, _check_model_access_helper, _can_object_call_model, _resolve_key_models_for_auth_check, can_key_call_model, can_team_access_model, can_project_access_model, can_user_call_model, _check_team_member_model_access

litellm/proxy/auth/user_api_key_auth.py: _enforce_key_and_fallback_model_access. SpecialModelNames is reused directly. Runtime alias/access-group resolution and cache/budget enforcement are not transplanted into preflight

## Literal execution evidence

Every JSON named below includes command array, cwd, required environment, literal stdout/stderr, child exit status and elapsed time

implementer-red.json: 14 failed, 144 passed, 1 warning in 13.24s, child exit 1. Failures include unsupported linked owners and finite models, missing explicit result scope, ignored key budgets and missing joined query. Shared wrapper saved JSON, then failed printing non-GBK stderr with UnicodeEncodeError; that is a separate wrapper failure, not the pytest outcome. Subsequent commands use PYTHONIOENCODING=utf-8

implementer-green-1.json: 2 failed, 175 passed, 71 deselected, 1 warning in 27.05s, child exit 1. Existing writer test required SELECT followed by a space while new query used a newline; query formatting was corrected, preserving the assertion. Pytest also emitted shared pytest-current cleanup WinError 5 at exit. Subsequent run used a new private basetemp, without deleting shared paths

implementer-green-2.json: 177 passed, 71 deselected, 1 warning in 26.46s, exit 0, empty stderr. Scope is all mapped reader tests plus native/subject/scope-filtered endpoint tests

implementer-lint-1.json and implementer-lint-final.json: Ruff all three changed Python files passed, exit 0, empty stderr

implementer-types-1.json: three strict type errors from the unbound frozenset.intersection descriptor's unknown generic parameter, exit 1. Changed to the typed first set's intersection method without casts or suppressions

implementer-types-final.json: strict reader source and mapped reader tests, 0 errors, 0 warnings, 0 notes, exit 0, empty stderr

git diff --check also returned exit 0. Parent runs the full regression and original data-plane suite once; this report does not claim those results

## Remaining risk

SQL projection shape and bounded joins are tested through an injected writer boundary, not against PostgreSQL. PostgreSQL schema/projection syntax was inspected against schema.prisma, but real database behavior remains unverified. Global router aliases, model-access-group label expansion and upstream/provider model identity are not resolved. New model_scope records only static literal facts and cannot authorize a request or migration. Controller must retain that distinction in final evidence and documentation
