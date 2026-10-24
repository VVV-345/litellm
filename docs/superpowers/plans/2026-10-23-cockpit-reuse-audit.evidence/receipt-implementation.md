# D2.2 credential receipt implementer report

## Scope and result

Implementation is frozen for controller review at base 61c3cb40ca6f37f7730c9ee27bc9981777d9f2c8 in the existing cockpit-reuse-audit worktree. No commits, push, build, deployment, database or real credential/provider access were performed by the implementer

Added an internal-only CockpitCredentialReceiptService with injected trusted subject reader, source record reader, existing credential download port, CredentialOwnership, CockpitAdminClient, secret deriver and clock. issue(subject_hash) returns a signed CredentialReceipt only after two complete observations agree. revalidate(subject_hash, encoded) verifies HMAC, subject binding and fixed 300-second lifetime before rereading all bindings twice

Supported: verified single-card subject and explicit restricted/unrestricted native model scopes. The trusted reader must freshly resolve native authorization; three native dynamic model sentinels are rejected by the local scope contract. Source must be available CLIProxyAPI/OpenAI Codex with safe auth basename, current ownership before credential read and after recomputed identity, an exact stored fingerprint-set match and no source ownership collisions. Same source filename in distinct isolated cards is allowed

Credential parsing accepts only explicit codex OAuth with nonempty access and refresh tokens. API-key/private-key/service-account/PAT/agent-identity fields, contradictory or unsupported modes, disabled material and non-Bearer token types fail closed. Target must have one exact mapped account and auth basename with no case/path/shared-file collisions, supported OAuth mapping, and observed metadata SHA256 and size equal to the trusted source raw bytes

Receipt binds subject/card/model scope, source version/name/state digest/fingerprint-set digest/content digest/size, target account/auth/file revision and manifest revision. New SecretPurpose.COCKPIT_CREDENTIAL_RECEIPT separates the HMAC key from management/gateway/identity purposes. Errors are fixed typed rejection codes and never carry dependency exception text or credential contents

## Explicit limits

This service has NO production HTTP registration or native subject adapter. Constructing TrustedCredentialScope is not provenance: a future trusted reader must map only the freshly verified NativeSubjectResult. No caller proof flag, credential bytes or digest may enter issue as authority. The successful owned_artifact_correspondence status proves owned artifact byte equality only. It is not provider-account identity, transformed credential compatibility, runtime usability, a completed migration, or request authorization

Existing cockpit_preflight target_identity_unverified, ready_to_migrate=False and routing/runtime blockers remain unchanged. The new receipt also keeps ready_to_migrate=False. Repeated reads detect observed changes, but are not an atomic cross-service snapshot and cannot prevent a later state change. Receipt expiry is inclusive at expires_at (rejected); a future issue time is rejected. No scope publication, ownership writes, credential writes, activation or traffic cutover occurs

## Changed files

- `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\account_pool\cockpit_credential_receipts.py`
  - SHA256 `c1a16239efedb93c41e95daa37315c2bee495c020031c7a3aafa19c418da665f`, new LF file
- `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\tests\account_pool\test_cockpit_credential_receipts.py`
  - SHA256 `e8e5ba385ce1bc9a3e89d545d9de07bfcea99ec0764f31be6e1d17e79b2d62a5`, new LF file
- `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\account_pool\shared\secrets.py`
  - SHA256 `dee70227a3c227b928ff1d0e34eba2073dbb562c35a7780a899ea774d9f6501a`, LF preserved
- `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\account_pool\cockpit_admin.py`
  - SHA256 `ba332878cdbb742fa85a0c8dbf295591b35263a2b0473cbe7e4443f020bbc0f8`, LF preserved
- `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\account_pool\cockpit_preflight.py`
  - SHA256 `7b994944dee6dabdcf85bb139eab1ddb99cfe6ec864d66458497555dcfe8d5ae`, LF preserved

Existing-source edits are one enum member and public aliases for existing parser/availability/auth-name/alias helpers. Private names and behavior are retained; the receipt service uses the public aliases. No schema changes are needed

## TDD and typed/lint execution

All commands used D:/Work2api/litellm/.venv, workspace/account-pool PYTHONPATH, PYTHONDONTWRITEBYTECODE=1, LITELLM_LOCAL_MODEL_COST_MAP=True and PYTHONIOENCODING=utf-8. run-command.py captured literal stdout, stderr and exit status in each JSON record. Full output is in those records, not the excerpts below

### implementer-red

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-impl-red"
]
```

Exit status: 1
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-red.json`

Literal final stdout excerpt:
```text
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_untrusted_or_malformed_receipts_cannot_authorize_reads[long_lifetime]
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_untrusted_or_malformed_receipts_cannot_authorize_reads[raw_token]
71 failed in 16.58s
```

Literal stderr:
```text

```

### implementer-green-1

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-impl-green-1"
]
```

Exit status: 0
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-green-1.json`

Literal final stdout excerpt:
```text
.......................................................................  [100%]
71 passed in 5.43s
```

Literal stderr:
```text

```

### implementer-types-1

```json
[
  "D:/Work2api/litellm/.venv/Scripts/basedpyright.exe",
  "account-pool/account_pool/cockpit_credential_receipts.py",
  "account-pool/account_pool/shared/secrets.py",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py"
]
```

Exit status: 1
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-types-1.json`

Literal final stdout excerpt:
```text
  c:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\account-pool\tests\account_pool\test_cockpit_credential_receipts.py:307:47 - error: Cannot access attribute "code" for class "CredentialReceipt"
    Attribute "code" is unknown (reportAttributeAccessIssue)
34 errors, 0 warnings, 0 notes
```

Literal stderr:
```text

```

### implementer-types-2

```json
[
  "D:/Work2api/litellm/.venv/Scripts/basedpyright.exe",
  "-p",
  ".superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/pyright.json"
]
```

Exit status: 0
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-types-2.json`

Literal final stdout excerpt:
```text
0 errors, 0 warnings, 0 notes
```

Literal stderr:
```text

```

### implementer-red-2

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "-k",
  "sentinels or denied_ownership or revoked_during_download",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-impl-red-2"
]
```

Exit status: 1
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-red-2.json`

Literal final stdout excerpt:
```text
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_native_model_sentinels_cannot_become_finite_receipt_scope[models5]
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_denied_ownership_never_downloads_source_credentials
7 failed, 1 passed, 71 deselected in 7.67s
```

Literal stderr:
```text

```

### implementer-green-2

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-impl-green-2"
]
```

Exit status: 0
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-green-2.json`

Literal final stdout excerpt:
```text
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 5.11s
```

Literal stderr:
```text

```

### implementer-lint-1

```json
[
  "D:/Work2api/litellm/.venv/Scripts/ruff.exe",
  "check",
  "account-pool/account_pool/cockpit_credential_receipts.py",
  "account-pool/account_pool/shared/secrets.py",
  "account-pool/account_pool/cockpit_admin.py",
  "account-pool/account_pool/cockpit_preflight.py",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py"
]
```

Exit status: 0
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-lint-1.json`

Literal final stdout excerpt:
```text
All checks passed!
```

Literal stderr:
```text

```

### implementer-types-final

```json
[
  "D:/Work2api/litellm/.venv/Scripts/basedpyright.exe",
  "-p",
  ".superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/pyright.json"
]
```

Exit status: 0
Raw command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-types-final.json`

Literal final stdout excerpt:
```text
0 errors, 0 warnings, 0 notes
```

Literal stderr:
```text

```

The initial 71 RED failures came from behavior tests invoking the missing module inside test execution, not collection errors. Initial GREEN passed 71. Follow-up RED proved six native sentinel cases and denied-ownership-before-download failed; the already-existing post-download revocation check passed. Final GREEN passed 79 in 5.11s. Final strict source/test type check reported 0 errors, 0 warnings, 0 notes; Ruff reported All checks passed!

Initial type run failed with 34 errors: incorrect import roots, private helper imports, deprecated AsyncIterator annotation and missing result narrowing in tests. Corrected the scoped extraPaths config, introduced concrete public aliases, used AsyncGenerator, made test fixture construction self-contained, and narrowed rejection unions. The initial failed record is retained

Preparation errors retained in tool history: attempted nonexistent auth_files.py/test_account_pool.py paths before finding their actual locations; an rg wildcard path was rejected on Windows; one no-op apply_patch with a nonmatching context was rejected. No bytes were changed by those failed preparation commands

## Reusable controller smoke fixture

Import account-pool/tests/account_pool/test_cockpit_credential_receipts.py and use async with harness() as (service, readers, ownership). SUBJECT/CARD are fixed fake identities. readers.current_scope, records, content, accounts, files, revision and now are controlled in-memory inputs; the real HTTP clients run through a GET-only MockTransport handler. No production-only helpers were added for tests

Controller owns independent review, regression, unchanged preflight/native checks, the four existing transaction artifact roles, rollback verification, evidence/schema updates and final commit. No full D2.2 completion claim is made

## Independent-review P2 correction: case-insensitive Go projection

Latest frozen result supersedes the initial 79-case result above: 100 receipt tests passed in 6.31s. Strict scoped types reported 0 errors, 0 warnings, 0 notes; Ruff and git diff --check passed. This correction only changes cockpit_credential_receipts.py and its mapped test. Earlier public aliases and existing preflight behavior are unchanged

Review showed that Go json.Unmarshal matches accountSpec JSON fields case-insensitively while the reused Pydantic projection ignores case-variant extras. A manifest containing canonical OAuth fields plus later AUTHID, AUTHKIND, ACCESSTOKENONLY, UPSTREAMAPIKEY or ID could produce a receipt for different account/auth semantics than Go sees. Top-level Accounts could similarly replace the accounts collection

Added receipt-local _canonical_target_fields validation before the permissive projection and auth-file metadata read. It rejects only noncanonical casefold matches for accounts at the root and id/authId/authKind/accessTokenOnly/upstreamApiKey in every account object, returning target_auth_unsupported. Canonical fields and unrelated extra fields, including nested metadata, remain accepted. This is a fail-closed supported-format boundary, not a rewrite of shared preflight parsing

TDD RED: 18 failed, 3 passed, 79 deselected. All dangerous colliding variants reproduced success before the fix; two missing-authId variants already failed closed and the unrelated metadata control passed. GREEN: all 100 tests passed, including aliases alone, aliases colliding with canonical fields, mixed-case spellings, top-level variants and unrelated metadata. No real Go process was run for this correction; Go semantics were verified from the reviewed accountSpec tags and the review reproduction, while the new regression tests exercise the real Manager HTTP/parsing/receipt path offline

### implementer-case-red

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "-k",
  "case_variant or unrelated_manifest_metadata",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-case-red"
]
```

Exit status: 1
Full literal command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-case-red.json`

Literal final stdout excerpt:
```text
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_case_variant_accounts_collection_cannot_replace_go_projection[True-ACCOUNTS]
FAILED account-pool\tests\account_pool\test_cockpit_credential_receipts.py::test_case_variant_accounts_collection_cannot_replace_go_projection[True-aCcOuNtS]
18 failed, 3 passed, 79 deselected in 11.68s
```

Literal stderr:
```text

```

### implementer-case-green

```json
[
  "D:/Work2api/litellm/.venv/Scripts/python.exe",
  "-m",
  "pytest",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py",
  "-q",
  "--basetemp=.superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/tmp-case-green"
]
```

Exit status: 0
Full literal command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-case-green.json`

Literal final stdout excerpt:
```text
........................................................................ [ 72%]
............................                                             [100%]
100 passed in 6.31s
```

Literal stderr:
```text

```

### implementer-case-types

```json
[
  "D:/Work2api/litellm/.venv/Scripts/basedpyright.exe",
  "-p",
  ".superpowers/sdd/2026-10-23-cockpit-reuse-audit/credential-receipts-20261024/pyright.json"
]
```

Exit status: 0
Full literal command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-case-types.json`

Literal final stdout excerpt:
```text
0 errors, 0 warnings, 0 notes
```

Literal stderr:
```text

```

### implementer-case-lint

```json
[
  "D:/Work2api/litellm/.venv/Scripts/ruff.exe",
  "check",
  "account-pool/account_pool/cockpit_credential_receipts.py",
  "account-pool/tests/account_pool/test_cockpit_credential_receipts.py"
]
```

Exit status: 0
Full literal command/stdout/stderr record: `C:\Users\42117\.codex\worktrees\cockpit-reuse-audit\litellm\.superpowers\sdd\2026-10-23-cockpit-reuse-audit\credential-receipts-20261024\implementer-case-lint.json`

Literal final stdout excerpt:
```text
All checks passed!
```

Literal stderr:
```text

```
