# Dashboard performance and recovery

Approved scope: render the current page immediately, idle preload other modules, isolate delay cache updates, keep cached configuration editable, and offer explicit recovery after deployment or chunk failure

Work directly on `CLIProxyAPI分支`. Baseline: `80056b807a`. No production deployment in this task. Commit after verification

1. Add regression tests for immediate rendering with slow/failed warmup, idle scheduling and cancellation, delay cache reuse and mutation isolation, cached forms and draft preservation
2. Make warmup non-blocking and sequential in idle slots; remove the duplicate sidebar-wide prefetch schedule, keep hover/focus prefetch and first-page log limits
3. Add an explicit read-only request option for diagnostic POST, preserve normal mutation invalidation; use the existing 60-second delay cache on remount and avoid duplicate interval requests
4. Seed card policy from the same-user list cache; refresh in the background without overwriting edited values or changing the draft's optimistic version; keep cached proxy options enabled
5. Embed a UI build identity, check the current HTML on foreground return, and handle chunk failures with visible retry/update actions. Never automatically reload. Track unsaved card forms in memory and warn before leaving; never persist credentials or drafts in browser storage
6. Run targeted Vitest, production build and source lint/type comparisons. Reconstruct the same four artifact roles and verify baseline/modified/rollback with identical regression inputs. Commit locally, report push/deploy status explicitly

## Local verification

All 81 targeted tests pass: 32 shared behavior regressions, 4 recovery integrations, 25 HTTP/cache tests, and 20 related settings/query components

Production source types and the touched test-file types pass separately. The Webpack production build exports 52 pages with one consistent build identity

The targeted ESLint comparison retains the baseline `max-params` error in `RuntimePolicyDialog.tsx`, with no new errors. Baseline has 41 warnings and modified has 46

Review caught an older background GET overwriting a successful policy save and a failed lazy component that could not recover through reset. The new regressions failed before the fixes and pass after them. Module retry now uses a manual reload protected by the unsaved-change confirmation; unrelated errors still use reset

No live gateway or production-browser acceptance was performed. Log pagination is unchanged. No push or deployment is part of this task. Baseline, patch reconstruction, rollback hashes, commands, and literal results are retained in the local verification artifacts
