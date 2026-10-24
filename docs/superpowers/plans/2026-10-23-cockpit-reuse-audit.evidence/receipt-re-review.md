# Scoped credential receipt P2 re-review

## Result

Original P2 closed. No new P1/P2 findings in the narrow correction. The original report is retained unchanged

Reviewed `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_credential_receipts.py:149-161,270-278` and the added mapped tests against the original review.patch. The production delta is the local `_canonical_target_fields` guard and its invocation before target projection parsing. No shared preflight behavior was changed

The guard rejects noncanonical spellings of root `accounts` and account `id`, `authId`, `authKind`, `accessTokenOnly`, `upstreamApiKey`, including canonical-plus-shadow collisions. It examines all accounts, so alternate spellings cannot hide shared-file/account collisions in a second account. Unrelated manifest fields and nested metadata are preserved. Values and malformed shapes remain subject to the existing typed projection checks

## Reviewed identities

`C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_credential_receipts.py`

SHA256 `c1a16239efedb93c41e95daa37315c2bee495c020031c7a3aafa19c418da665f`

`C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/tests/account_pool/test_cockpit_credential_receipts.py`

SHA256 `e8e5ba385ce1bc9a3e89d545d9de07bfcea99ec0764f31be6e1d17e79b2d62a5`

Both matched the requested candidate hashes

## Executed narrow offline check

Ran `D:/Work2api/litellm/.venv/Scripts/python.exe -` in `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm` with workspace/account-pool PYTHONPATH, PYTHONDONTWRITEBYTECODE=1, LITELLM_LOCAL_MODEL_COST_MAP=True and PYTHONIOENCODING=utf-8

Imported the existing fake-only test harness. For each original shadow-field reproduction, first issued a canonical receipt, then injected the shadow and checked both fresh issue and revalidation of that prior receipt. Added the upstream API-key and root accounts shadows. Finally checked canonical fields plus unrelated top-level and nested metadata. Assertions passed, exit 0, stderr empty

Literal stdout:

```text
{"case": "AUTHID", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "AUTHKIND", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "ACCESSTOKENONLY", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "ID", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "UPSTREAMAPIKEY", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "ACCOUNTS", "issue": "target_auth_unsupported", "revalidate": "target_auth_unsupported"}
{"case": "canonical_with_unrelated_metadata", "issue": "owned_artifact_correspondence", "revalidate": "owned_artifact_correspondence"}
```

Inspected existing evidence, not rerun: implementer-case-red exit 1 with `18 failed, 3 passed, 79 deselected in 11.68s`; implementer-case-green exit 0 with `100 passed in 6.31s`; implementer-case-types exit 0 with `0 errors, 0 warnings, 0 notes`; implementer-case-lint exit 0 with `All checks passed!`. All four recorded stderr streams were empty

An auxiliary patch-comparison read initially exited 1 because pathlib used the Windows GBK default codec. Repeated with explicit UTF-8 decoding, exit 0, and confirmed the bounded production delta above

No implementation edits, commits, broad test reruns, real credential/service access or Go runtime runs were performed. This closes the specific persisted-manifest parser discrepancy, not the deferred production adapter, migration or live authorization work