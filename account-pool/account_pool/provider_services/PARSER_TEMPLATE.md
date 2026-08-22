<!-- 本文件规定新增渠道解析器时需要实现的目录、字段、安全边界和测试契约。 -->

# Provider Parser Template

本文说明新增渠道解析器时必须实现的目录结构、数据字段、安全边界和测试契约

## Directory Contract

Each provider implementation lives in `provider_services/{provider_id}/`:

```text
manifest.py  capability declarations and default API base
schemas.py   typed upstream response models
client.py    URL validation, authentication, HTTP calls, and safe failures
service.py   conversion to Account Pool domain results
parser.py    conversion from validated provider data to the unified parser contract
public_metadata.py optional credential-free metadata source registration
fixtures/    sanitized provider responses for contract tests, when needed
```

The validation registry depends only on `ProviderService`. The credential-free parser registry depends on immutable parser
registrations. Provider-specific branching must remain inside the provider directory.

## Credential-Free Background Source

Only add `public_metadata.py` when the provider has a stable official endpoint that can return useful metadata without an API key,
cookie, session, or account token. Register `RegisteredPublicMetadataSource` with the provider IDs, an existing parser ID, and an
async fetch function. Providers without such an endpoint remain unregistered; the worker must not infer balances, subscriptions, or
prices from marketing pages or public model names.

The persistent task contains only task, channel, parser-run and provider IDs plus execution state. The worker resolves the current
channel URL from the PostgreSQL catalog at execution time. URL, Key, credential reference, request and response bodies, cookies,
headers, and upstream error text must not enter the queue or operational events. A source result containing `key_fingerprint` is
rejected as unsafe.

Public tasks use PostgreSQL claiming with `FOR UPDATE SKIP LOCKED`, bounded exponential retry, heartbeat, and stale-worker recovery.
Each retry receives a new parser-run ID. Source transport failures are retryable; invalid or unsupported responses are persisted as a
typed parser run and then complete as a permanent task failure.

## Selection

Selection order is explicit parser, exact Provider plus normalized HTTPS origin, declared OpenAI-compatible fallback, then manual
input. Selection request models must not accept keys or credentials. An unknown explicit parser goes to manual correction instead of
silently selecting another automatic parser.

## Manifest

Declare a stable `provider_id`, display name, default API base, LiteLLM provider prefix, and every capability as `supported`, `unsupported`, or `unavailable`. Do not claim that an OpenAI-compatible endpoint supports billing merely because it supports `/models`.

## Input and Credential Lifetime

Provider validation receives `api_base`, an in-memory `SecretStr`, and an optional group. The key may be sent only to the validated provider origin. It must not enter domain snapshots, database rows, JSON exports, exceptions, logs, API responses, fixtures, or task payloads.

Clients must use HTTPS by default, reject URL user information, query parameters, fragments, cross-origin redirects, non-public targets, and oversized responses. A production-grade arbitrary-host implementation also needs a transport that pins the validated IP through connection establishment to close DNS rebinding between validation and connect.

Validation failures use stable typed codes for configuration, authentication, transport, upstream response, empty model visibility,
and unsupported providers. Parser status must be derived from those codes, never from localized display messages.

## Subscription Output

When the provider exposes the account's actual subscription, extract:

- stable plan ID and plan name
- status, start, and expiration timestamps
- included provider model IDs and normalized Account Pool model IDs
- balance and unit
- channel and model concurrency
- 5-hour, weekly, monthly, and custom quota windows

Unknown values remain null. Do not convert unknown values to zero or unlimited.

## Metered Output

For each provider group, extract:

- stable group ID and name
- available models
- source currency and unit
- input, output, cache-read, and cache-write prices
- group multiplier
- effective price after applying the provider's documented multiplier semantics
- group or model concurrency

Use decimal fixed-point values for money. Preserve source price, unit, multiplier, normalized per-million-token price, and conversion evidence separately.

## Billing Routes

Create a billing route only when Account Pool can select a distinct key, URL, deployment, provider group, or verified request parameter. A subscription and metered price appearing together is evidence, not proof that Account Pool can choose the charge mode.

## Models and Unresolved Fields

Preserve provider model ID, LiteLLM model name, and public model name as separate fields. Record unsupported, missing, invalid, or ambiguous fields as structured unresolved paths with retryability and a safe operator message.

## Snapshots

Parser snapshots are keyed by `channel_id` and include schema version, parser ID and version, run ID, timestamp, status, raw normalized result, effective overridden result, capabilities, unresolved fields, safe evidence, and warnings. They never contain URL, key, credential reference, authorization headers, cookies, or unfiltered upstream bodies.

## Contract Tests

Every provider must test successful parsing, authentication rejection, invalid response handling, URL and redirect safety, response-size limits, key non-disclosure, unsupported capability declarations, and representative sanitized fixtures. Tests must use injected transports and resolvers unless a separate controlled integration test explicitly requires a real provider.
