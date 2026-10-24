# Credential receipt review

## Scope

Reviewed the five-file patch against base `61c3cb40ca6f37f7730c9ee27bc9981777d9f2c8`, the live files, agreed brief, implementer report, existing ownership/identity code and Cockpit raw-byte metadata/runtime parser contract. All five live SHA256 hashes matched the implementer report at review time. No production edits, commits, broad test runs, real credentials, services or provider calls

## Spec and security: one P2

### [P2] Reject case-insensitive shadow fields before interpreting the target manifest

Location: `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_credential_receipts.py:261-270`

`CockpitPreflightManifest.model_validate(snapshot.data)` uses the existing case-sensitive Pydantic projection with ignored extra fields. Cockpit instead decodes the persisted raw manifest using Go `encoding/json` into `accountSpec`, whose JSON field matching is case-insensitive. Therefore a persisted account containing canonical `authId: "target.json"` followed by `AUTHID: "other.json"` is accepted here as a correspondence to target.json even though Cockpit maps that account to other.json. Likewise, later `AUTHKIND: "api_key"`, `ACCESSTOKENONLY: true` or `ID: OTHER` can override the target mode/account for Cockpit while the receipt checks continue using the earlier canonical fields

This violates the bounded contract's unique target account/auth mapping and unsupported-mode rejection. It does not require forging a receipt: the service signs the incorrectly interpreted projection itself, and repeated observations plus revalidation accept the same stable manifest. The finding concerns the persisted artifact mapping, not live authorization or the intentionally deferred atomic snapshot guarantee

Cockpit evidence: `C:/Users/42117/.codex/worktrees/cockpit-backend-replacement/sidecars/cockpit-cliproxy/manifest_admin.go:351-363` returns the raw stored manifest, its validation at lines 237-244 unmarshals into `manifest` without canonicalizing keys, and `C:/Users/42117/.codex/worktrees/cockpit-backend-replacement/sidecars/cockpit-cliproxy/manifest_policy.go:333-342,784-793` defines `accountSpec` tags and performs the same struct decoding. The shared Python parser is at `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_preflight.py:117-130`

Fix locally at the receipt trust boundary: reject noncanonical or case-colliding spellings of security-relevant manifest/account fields before applying the permissive shared projection, including the root accounts key. Preserve unrelated legitimate manifest fields and existing preflight behavior. Add regression cases for shadowed account ID, auth ID, auth mode, token-only/API-key flags and root accounts

## Offline reproduction

Used the existing fake-only fixture and real receipt/HTTP-client/ownership logic. Appending each uppercase field to the fixture account makes it appear later in the HTTP JSON. The standalone command exited 0 with no stderr. No Go runtime test was executed; the cross-parser interpretation is confirmed by the cited source and encoding/json struct semantics

Command: `D:/Work2api/litellm/.venv/Scripts/python.exe -`, run from `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm`, with PYTHONPATH set to that workspace plus its account-pool directory, PYTHONDONTWRITEBYTECODE=1, LITELLM_LOCAL_MODEL_COST_MAP=True and PYTHONIOENCODING=utf-8. Stdin:

```python
import asyncio, importlib.util, sys, json
from pathlib import Path
p = Path('account-pool/tests/account_pool/test_cockpit_credential_receipts.py')
spec = importlib.util.spec_from_file_location('receipt_review_fixture', p)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
async def main():
    for key, value in [('AUTHID', 'other.json'), ('AUTHKIND', 'api_key'), ('ACCESSTOKENONLY', True), ('ID', str(m.OTHER))]:
        async with m.harness() as (service, readers, ownership):
            readers.accounts[0][key] = value
            result = await service.issue(m.SUBJECT)
            replay = await service.revalidate(m.SUBJECT, result.model_dump_json())
            print(json.dumps({'case': key, 'issue': result.status, 'revalidate': replay.status, 'target_auth_id': result.payload.bindings.target_auth_id, 'reads': len(readers.calls)}))
asyncio.run(main())
```

Literal stdout:

```text
{"case": "AUTHID", "issue": "owned_artifact_correspondence", "revalidate": "owned_artifact_correspondence", "target_auth_id": "target.json", "reads": 12}
{"case": "AUTHKIND", "issue": "owned_artifact_correspondence", "revalidate": "owned_artifact_correspondence", "target_auth_id": "target.json", "reads": 12}
{"case": "ACCESSTOKENONLY", "issue": "owned_artifact_correspondence", "revalidate": "owned_artifact_correspondence", "target_auth_id": "target.json", "reads": 12}
{"case": "ID", "issue": "owned_artifact_correspondence", "revalidate": "owned_artifact_correspondence", "target_auth_id": "target.json", "reads": 12}
```

The earlier combined Python/Go-availability probe exited 1 after producing the issue-only results because Go was not on PATH. The standalone corrected probe above adds replay checks and records its own exit 0

## Standards and remaining security checks

No separate P1/P2 standards or maintainability findings. The small shared aliases retain existing implementations. Source provenance requires owned fingerprints before download, recomputes the exact fingerprint set from server-read bytes and rechecks ownership afterward. Source/target byte comparison uses SHA256 and size rather than conflating raw-byte hashes with identity HMACs. Purpose-separated HMAC covers the version/purpose, subject/card/models, source state/version/fingerprints/content and target account/file/manifest versions. The fixed lifetime, future/expiry checks, current repeated observations, typed fixed errors and unchanged migration/runtime blockers match the agreed internal-only boundary

Inspected, not rerun, the implementer evidence: initial RED 71 missing-feature failures, follow-up RED 7 genuine boundary failures with 1 passing ownership-revocation case, final GREEN 79 passed, Ruff clean and scoped strict type check 0 errors. Those tests do not cover the parser discrepancy above. No claim of provider identity, runtime readiness or complete D2.2 migration acceptance

Summary: standards 0 P1/P2 findings; spec/security 1 P2, requiring correction before accepting the receipt contract