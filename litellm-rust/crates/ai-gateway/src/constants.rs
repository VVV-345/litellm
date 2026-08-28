//! Crate-level constants for the ai-gateway.
//!
//! Per `litellm-rust/CLAUDE.md`, magic numbers and fixed strings live here
//! (the Rust mirror of Python's `litellm/constants.py`), not inline in feature
//! modules. Env-overridable tunables keep their `DEFAULT_*` value here; the env
//! read + fallback happens at the host/config layer.

/// Default LiteLLM control-plane base URL for request-log egress when
/// `LITELLM_PROXY_BASE_URL` is unset.
pub(crate) const DEFAULT_PROXY_BASE_URL: &str = "http://localhost:4000";

/// The logs ingest path appended to the proxy base. Not a tunable; it is the
/// proxy's API contract (the rust-control-plane router on the Python proxy).
pub(crate) const RUST_CONTROL_PLANE_LOGS_PATH: &str = "/v1/rust_control_plane/logs";

/// Default bounded channel depth for the log-egress worker.
/// Override: `LITELLM_LOG_CHANNEL_CAPACITY`.
pub(crate) const DEFAULT_CHANNEL_CAPACITY: usize = 4096;

/// Default max records POSTed per request to the control plane.
/// Override: `LITELLM_LOG_BATCH_SIZE`.
pub(crate) const DEFAULT_MAX_BATCH_SIZE: usize = 256;

/// Default partial-batch flush cadence, in ms.
/// Override: `LITELLM_LOG_FLUSH_INTERVAL_MS`.
pub(crate) const DEFAULT_FLUSH_INTERVAL_MS: u64 = 500;

/// Provider attributed to realtime sessions in the logging payload.
#[cfg(feature = "server")]
pub(crate) const DEFAULT_PROVIDER: &str = "openai";

pub(crate) const DEFAULT_RESPONSES_WS_CONNECT_TIMEOUT_SECS: u64 = 10;
pub(crate) const DEFAULT_RESPONSES_WS_IDLE_TIMEOUT_SECS: u64 = 300;

pub(crate) const ACCOUNT_POOL_ENABLED_ENV: &str = "ACCOUNT_POOL_RUST_ENABLED";
pub(crate) const ACCOUNT_POOL_CONFIG_URL_ENV: &str = "ACCOUNT_POOL_RUNTIME_CONFIG_URL";
pub(crate) const ACCOUNT_POOL_TOKEN_ENV: &str = "ACCOUNT_POOL_INTERNAL_TOKEN";
pub(crate) const ACCOUNT_POOL_REFRESH_INTERVAL_ENV: &str =
    "ACCOUNT_POOL_RUNTIME_REFRESH_INTERVAL_SECONDS";
pub(crate) const DEFAULT_ACCOUNT_POOL_CONFIG_URL: &str =
    "http://127.0.0.1:4100/internal/runtime-config";
pub(crate) const DEFAULT_ACCOUNT_POOL_REFRESH_INTERVAL_SECS: u64 = 5;
pub(crate) const DEFAULT_ACCOUNT_POOL_CONNECT_TIMEOUT_SECS: u64 = 3;
pub(crate) const DEFAULT_ACCOUNT_POOL_REQUEST_TIMEOUT_SECS: u64 = 10;
pub(crate) const ACCOUNT_POOL_REDIS_URL_ENV: &str = "ACCOUNT_POOL_REDIS_URL";
pub(crate) const DEFAULT_ACCOUNT_POOL_REDIS_URL: &str = "redis://127.0.0.1:6379/0";
pub(crate) const ACCOUNT_POOL_LITELLM_URL_ENV: &str = "ACCOUNT_POOL_LITELLM_URL";
pub(crate) const DEFAULT_ACCOUNT_POOL_LITELLM_URL: &str = "http://127.0.0.1:4000";
pub(crate) const ACCOUNT_POOL_PRE_AUTH_ENV: &str = "ACCOUNT_POOL_GATEWAY_PRE_AUTH";
pub(crate) const ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS_ENV: &str =
    "ACCOUNT_POOL_GATEWAY_PRE_AUTH_CACHE_SECONDS";
pub(crate) const DEFAULT_ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS: u64 = 30;
pub(crate) const DEFAULT_ACCOUNT_POOL_PROXY_CONNECT_TIMEOUT_SECS: u64 = 5;
pub(crate) const DEFAULT_ACCOUNT_POOL_PROXY_REQUEST_TIMEOUT_SECS: u64 = 120;
pub(crate) const DEFAULT_ACCOUNT_POOL_STREAM_CHANNEL_CAPACITY: usize = 16;
pub(crate) const MAXIMUM_ACCOUNT_POOL_PRE_AUTH_CACHE_ENTRIES: usize = 10_000;

/// HTTP path for the non-streaming Anthropic Messages route.
#[cfg(feature = "server")]
pub(crate) const MESSAGES_ROUTE_PATH: &str = "/v1/messages";

/// Request headers owned by the gateway and never forwarded upstream.
#[cfg(feature = "server")]
pub(crate) const MESSAGES_HEADERS_NOT_FORWARDED: &[&str] =
    &["authorization", "connection", "content-length", "host"];
