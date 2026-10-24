# Final review: bounded complex native subject scope

Current: D2.2 complex native scope candidate / no actionable P1 or P2 findings / controller regression and transaction verification remain the commit gates

Reviewed the uncommitted continuation against base `d06bd940ecfe69f7779486001ad648162dfa139a` in `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm`, using review.patch, brief.md, implementer-report.md, live source, native authentication helpers and schema.prisma. The review was read-only except this report; no agents, commits, broad test reruns or external services were used

## Standards

No actionable P1/P2 findings. The change stays in the existing reader, its mapped tests and generated response schema. Frozen typed scopes and explicit result validation prevent a verified result without its model scope. The query selects only the bound key and narrow owner facts; it does not fetch owner passwords, emails, raw metadata or the virtual-key secret table. Errors retain the existing non-echoing public statuses

## Spec

No actionable P1/P2 findings within the expressly bounded static-label contract. `account_pool_cockpit_subject.py:161-195` intersects nonempty key, applicable personal-user, team and project model lists. Empty native lists add no restriction; an empty intersection stays unresolved rather than becoming unrestricted. Personal-user models are applied only without a team, matching `auth_checks.py:953-960`. Sentinels and wildcard expressions fail closed

Owner identity, parent-team consistency, project/team null equality and membership identity are checked. Requiring an existing membership and rejecting any membership budget reference is conservative rather than a runtime-auth regression: actual member model restrictions come from the referenced budget table (`auth_checks.py:5078-5118`). Rejecting a nonapplicable user's malformed/sentinel model list also narrows support without expanding permissions

The existing endpoint retains `subject.model_scope` publicly while forwarding only the card and candidates to Manager (`account_pool_endpoints.py:721-746`). Its administrator requirement, no-store response, single-card response validation and `ready_to_migrate: Literal[False]` remain intact. Generated NativeModelScope fields match the response model

## SQL and verification

The joined statement uses `$1` for the bound key and repeats tombstone exclusion in that same statement. Key, user, team and project IDs are primary keys; membership has the `(user_id, team_id)` composite primary key. Thus the joins cannot introduce an arbitrary second owner row under the declared schema. No referenced column or SQL type mismatch was found

A fresh offline standard-library AST/schema check returned exit 0 with literal stdout:

```text
Schema reference check: PASS; 76 distinct qualified columns, 6 array predicates, 8 JSON predicates
All five joined base-table IDs are primary/unique keys; membership composite key is declared in schema.prisma
No PostgreSQL engine or runtime auth request was executed
Subject SHA256: 4a1361786e12569483b4bfcc888c90adf26ffc161a0d55114df15e1ec28ac6be
```

The implementer's recorded 177-pass focused run and clean strict-type run were inspected as existing evidence, not rerun or represented as fresh reviewer results

## Limits and follow-up

The SQL tests stub the already-projected row and inspect query text. They cannot validate PostgreSQL execution or Prisma's actual JSON/array decoding. Before this reader is relied on against a live database, exercise this exact statement on an isolated migrated PostgreSQL database with unrestricted, restricted, missing-owner, blocked-owner, membership-budget and tombstone rows; assert the returned Boolean/JSON shapes and fail-closed outcomes. No concrete SQL defect was found during this review

The result is not full runtime authorization. Global alias/access-group label expansion, provider identity, global policy and other dynamic checks remain unresolved. Key rate/concurrency fields are still outside this static result, as before this diff, so `kind=unrestricted` must mean only no supported static model-list restriction, never unrestricted request permission. The preserved false readiness/runtime-unverified boundary is essential

Verdict: suitable for this bounded preflight-only commit after the controller's pending regression and transaction gates pass. Standards: 0 actionable findings. Spec: 0 actionable findings. No traffic cutover or migration approval