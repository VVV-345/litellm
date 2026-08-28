//! Proxies account-pool requests to LiteLLM after Rust selects a deployment.

use std::collections::HashMap;
use std::fmt::{Display, Formatter};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use bytes::Bytes;
use futures_util::StreamExt;
use httpdate::parse_http_date;
use reqwest::{Client, Url};
use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::sync::{Mutex, mpsc};
use tokio::time::{Instant, MissedTickBehavior};
use uuid::Uuid;

use super::AccountPoolRuntime;
use super::scheduler::AccountPoolScheduler;
use super::store::{RedisLeaseStore, RedisStoreError};
use super::types::{AcquireRequest, AcquireResult, AcquiredLease, Lease, SettleRequest};
use crate::constants::{
    ACCOUNT_POOL_CONFIG_URL_ENV, ACCOUNT_POOL_LITELLM_URL_ENV,
    ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS_ENV, ACCOUNT_POOL_PRE_AUTH_ENV, ACCOUNT_POOL_REDIS_URL_ENV,
    ACCOUNT_POOL_TOKEN_ENV, DEFAULT_ACCOUNT_POOL_CONFIG_URL,
    DEFAULT_ACCOUNT_POOL_CONNECT_TIMEOUT_SECS, DEFAULT_ACCOUNT_POOL_LITELLM_URL,
    DEFAULT_ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS, DEFAULT_ACCOUNT_POOL_PROXY_CONNECT_TIMEOUT_SECS,
    DEFAULT_ACCOUNT_POOL_PROXY_REQUEST_TIMEOUT_SECS, DEFAULT_ACCOUNT_POOL_REDIS_URL,
    DEFAULT_ACCOUNT_POOL_REQUEST_TIMEOUT_SECS, DEFAULT_ACCOUNT_POOL_STREAM_CHANNEL_CAPACITY,
    MAXIMUM_ACCOUNT_POOL_PRE_AUTH_CACHE_ENTRIES,
};

const REQUEST_HEADER_ALLOWLIST: &[&str] = &[
    "accept",
    "authorization",
    "content-type",
    "user-agent",
    "x-litellm-api-key",
    "x-request-id",
];
const RESPONSE_HEADER_DENYLIST: &[&str] = &[
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "transfer-encoding",
];
const MAXIMUM_RETRY_AFTER_SECONDS: f64 = 86_400.0;
const CONTROL_PLANE_EVENT_CHANNEL_CAPACITY: usize = 1_024;

#[derive(Debug, Clone)]
pub struct AccountPoolProxySettings {
    redis_url: String,
    litellm_url: Url,
    pre_auth: bool,
    pre_auth_cache_ttl: Duration,
    control_plane: Option<AccountPoolControlPlaneSettings>,
}

impl AccountPoolProxySettings {
    pub fn from_env() -> Result<Self, AccountPoolProxySettingsError> {
        let redis_url = std::env::var(ACCOUNT_POOL_REDIS_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_ACCOUNT_POOL_REDIS_URL.to_string());
        let litellm_url = std::env::var(ACCOUNT_POOL_LITELLM_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_ACCOUNT_POOL_LITELLM_URL.to_string());
        let pre_auth = std::env::var(ACCOUNT_POOL_PRE_AUTH_ENV)
            .map(|value| !value.trim().eq_ignore_ascii_case("false"))
            .unwrap_or(true);
        let cache_seconds = std::env::var(ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS_ENV)
            .ok()
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| AccountPoolProxySettingsError::InvalidCacheTtl)
            })
            .transpose()?
            .unwrap_or(DEFAULT_ACCOUNT_POOL_PRE_AUTH_CACHE_SECONDS);
        Self::new_with_control_plane(
            redis_url,
            litellm_url,
            pre_auth,
            Duration::from_secs(cache_seconds),
            Some(AccountPoolControlPlaneSettings::from_env()?),
        )
    }

    pub fn new(
        redis_url: String,
        litellm_url: String,
        pre_auth: bool,
        pre_auth_cache_ttl: Duration,
    ) -> Result<Self, AccountPoolProxySettingsError> {
        Self::new_with_control_plane(redis_url, litellm_url, pre_auth, pre_auth_cache_ttl, None)
    }

    fn new_with_control_plane(
        redis_url: String,
        litellm_url: String,
        pre_auth: bool,
        pre_auth_cache_ttl: Duration,
        control_plane: Option<AccountPoolControlPlaneSettings>,
    ) -> Result<Self, AccountPoolProxySettingsError> {
        if redis_url.trim().is_empty() {
            return Err(AccountPoolProxySettingsError::InvalidRedisUrl);
        }
        let litellm_url = Url::parse(litellm_url.trim())
            .map_err(|_| AccountPoolProxySettingsError::InvalidLiteLLMUrl)?;
        if !matches!(litellm_url.scheme(), "http" | "https") || litellm_url.host_str().is_none() {
            return Err(AccountPoolProxySettingsError::InvalidLiteLLMUrl);
        }
        Ok(Self {
            redis_url: redis_url.trim().to_string(),
            litellm_url,
            pre_auth,
            pre_auth_cache_ttl,
            control_plane,
        })
    }
}

#[derive(Debug, Clone)]
struct AccountPoolControlPlaneSettings {
    endpoint: Url,
    token: Arc<str>,
}

impl AccountPoolControlPlaneSettings {
    fn from_env() -> Result<Self, AccountPoolProxySettingsError> {
        let endpoint = std::env::var(ACCOUNT_POOL_CONFIG_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_ACCOUNT_POOL_CONFIG_URL.to_string());
        let token = std::env::var(ACCOUNT_POOL_TOKEN_ENV).unwrap_or_default();
        Self::new(endpoint, token)
    }

    fn new(endpoint: String, token: String) -> Result<Self, AccountPoolProxySettingsError> {
        if token.trim().is_empty() {
            return Err(AccountPoolProxySettingsError::MissingInternalToken);
        }
        let endpoint = Url::parse(endpoint.trim())
            .map_err(|_| AccountPoolProxySettingsError::InvalidRuntimeConfigUrl)?;
        if !matches!(endpoint.scheme(), "http" | "https") || endpoint.host_str().is_none() {
            return Err(AccountPoolProxySettingsError::InvalidRuntimeConfigUrl);
        }
        Ok(Self {
            endpoint,
            token: Arc::from(token.trim()),
        })
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AccountPoolProxySettingsError {
    #[error("ACCOUNT_POOL_REDIS_URL is required when the Rust account pool is enabled")]
    InvalidRedisUrl,
    #[error("ACCOUNT_POOL_LITELLM_URL must be an HTTP or HTTPS URL")]
    InvalidLiteLLMUrl,
    #[error("ACCOUNT_POOL_GATEWAY_PRE_AUTH_CACHE_SECONDS must be a non-negative integer")]
    InvalidCacheTtl,
    #[error("ACCOUNT_POOL_INTERNAL_TOKEN is required when the Rust account pool is enabled")]
    MissingInternalToken,
    #[error("ACCOUNT_POOL_RUNTIME_CONFIG_URL must be an HTTP or HTTPS URL")]
    InvalidRuntimeConfigUrl,
}

#[derive(Debug, Error)]
pub enum AccountPoolProxyStartError {
    #[error(transparent)]
    Redis(#[from] RedisStoreError),
    #[error("failed to build the account-pool LiteLLM client: {0}")]
    HttpClient(reqwest::Error),
    #[error("failed to build the account-pool control-plane client: {0}")]
    ControlPlaneHttpClient(reqwest::Error),
}

#[derive(Clone)]
pub enum AccountPoolProxyRuntime {
    Disabled,
    Ready(Arc<AccountPoolProxy>),
    Failed,
}

impl AccountPoolProxyRuntime {
    pub fn disabled() -> Self {
        Self::Disabled
    }

    pub fn failed() -> Self {
        Self::Failed
    }

    pub async fn start(
        runtime: AccountPoolRuntime,
        settings: AccountPoolProxySettings,
    ) -> Result<Self, AccountPoolProxyStartError> {
        AccountPoolProxy::start(runtime, settings)
            .await
            .map(|proxy| Self::Ready(Arc::new(proxy)))
    }

    pub fn enabled(&self) -> bool {
        !matches!(self, Self::Disabled)
    }

    pub fn ready(&self) -> bool {
        !matches!(self, Self::Failed)
    }

    pub async fn forward(
        &self,
        request: AccountPoolProxyRequest,
    ) -> Result<AccountPoolProxyResponse, AccountPoolProxyError> {
        match self {
            Self::Ready(proxy) => proxy.forward(request).await,
            Self::Disabled | Self::Failed => Err(AccountPoolProxyError::NotReady),
        }
    }
}

#[derive(Clone)]
pub struct AccountPoolProxy {
    scheduler: AccountPoolScheduler,
    http: Client,
    litellm_url: Url,
    pre_auth: Option<PreAuthCache>,
    event_sender: Option<mpsc::Sender<ControlPlaneEvent>>,
}

struct StreamForwardContext {
    acquired: AcquiredLease,
    status: u16,
    retry_after_seconds: Option<f64>,
    started: Instant,
    timeout: Duration,
}

#[derive(Clone)]
struct AccountPoolControlPlane {
    http: Client,
    endpoint: Url,
    token: Arc<str>,
}

enum ControlPlaneEvent {
    RequestActivity(Lease),
    Settlement {
        lease: Lease,
        settlement: SettleRequest,
    },
}

#[derive(Serialize)]
struct SettlementEventPayload {
    lease: Lease,
    settlement: SettleRequest,
}

impl AccountPoolControlPlane {
    fn new(settings: AccountPoolControlPlaneSettings) -> Result<Self, AccountPoolProxyStartError> {
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_CONNECT_TIMEOUT_SECS,
            ))
            .timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_REQUEST_TIMEOUT_SECS,
            ))
            .build()
            .map_err(AccountPoolProxyStartError::ControlPlaneHttpClient)?;
        Ok(Self {
            http,
            endpoint: settings.endpoint,
            token: settings.token,
        })
    }

    async fn run(self, mut receiver: mpsc::Receiver<ControlPlaneEvent>) {
        while let Some(event) = receiver.recv().await {
            match event {
                ControlPlaneEvent::RequestActivity(lease) => {
                    self.post("/internal/request-activity", &lease).await;
                }
                ControlPlaneEvent::Settlement { lease, settlement } => {
                    self.post(
                        "/internal/settlement-event",
                        &SettlementEventPayload { lease, settlement },
                    )
                    .await;
                }
            }
        }
    }

    async fn post<T: Serialize>(&self, path: &str, payload: &T) {
        let _ = self
            .http
            .post(self.url(path))
            .header("x-account-pool-token", self.token.as_ref())
            .json(payload)
            .send()
            .await;
    }

    fn url(&self, path: &str) -> Url {
        let mut url = self.endpoint.clone();
        url.set_path(path);
        url.set_query(None);
        url
    }
}

impl AccountPoolProxy {
    async fn start(
        runtime: AccountPoolRuntime,
        settings: AccountPoolProxySettings,
    ) -> Result<Self, AccountPoolProxyStartError> {
        let AccountPoolProxySettings {
            redis_url,
            litellm_url,
            pre_auth,
            pre_auth_cache_ttl,
            control_plane,
        } = settings;
        let store = RedisLeaseStore::connect(&redis_url).await?;
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_PROXY_CONNECT_TIMEOUT_SECS,
            ))
            .timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_PROXY_REQUEST_TIMEOUT_SECS,
            ))
            .build()
            .map_err(AccountPoolProxyStartError::HttpClient)?;
        let control_plane = control_plane
            .map(AccountPoolControlPlane::new)
            .transpose()?;
        let event_sender = control_plane.map(|control_plane| {
            let (sender, receiver) = mpsc::channel(CONTROL_PLANE_EVENT_CHANNEL_CAPACITY);
            tokio::spawn(async move {
                control_plane.run(receiver).await;
            });
            sender
        });
        Ok(Self {
            scheduler: AccountPoolScheduler::new(runtime, store),
            http,
            litellm_url,
            pre_auth: pre_auth.then_some(PreAuthCache::new(pre_auth_cache_ttl)),
            event_sender,
        })
    }

    async fn forward(
        &self,
        request: AccountPoolProxyRequest,
    ) -> Result<AccountPoolProxyResponse, AccountPoolProxyError> {
        self.pre_authorize(&request.headers).await?;
        let body = parse_request_body(&request.body)?;
        let public_model = request_model(&body)?;
        let request_id = request_id(&request.headers);
        let upstream_url = self.upstream_url(&request.path)?;
        let acquired = self
            .scheduler
            .acquire(&AcquireRequest {
                request_id: request_id.clone(),
                model: public_model.clone(),
                estimated_tokens: estimated_tokens(&body),
            })
            .await
            .map_err(|_| AccountPoolProxyError::NoRoute {
                model: public_model.clone(),
                reasons: vec!["scheduler_unavailable".to_string()],
            })?;
        let acquired = match acquired {
            AcquireResult::Acquired(acquired) => acquired,
            AcquireResult::Unavailable {
                model,
                reason_codes,
            } => {
                return Err(AccountPoolProxyError::NoRoute {
                    model,
                    reasons: reason_codes,
                });
            }
        };
        self.record_event(ControlPlaneEvent::RequestActivity(acquired.lease.clone()));
        let forwarded_body = forwarded_body(&body, &acquired.lease, &request_id);
        let timeout = match lease_timeout(&acquired.lease) {
            Ok(timeout) => timeout,
            Err(error) => {
                self.settle_and_release(
                    &acquired.lease,
                    failed_settlement(&acquired.lease, Duration::ZERO, "lease_expired"),
                )
                .await;
                return Err(error);
            }
        };
        let mut upstream_request = self
            .http
            .post(upstream_url)
            .json(&forwarded_body)
            .timeout(timeout);
        for (name, value) in request
            .headers
            .iter()
            .filter(|(name, _)| allowed_header(name))
        {
            upstream_request = upstream_request.header(name, value);
        }
        let started = Instant::now();
        let upstream = match upstream_request.send().await {
            Ok(response) => response,
            Err(_) => {
                self.settle_and_release(
                    &acquired.lease,
                    failed_settlement(&acquired.lease, started.elapsed(), "proxy_transport"),
                )
                .await;
                return Err(AccountPoolProxyError::UpstreamUnavailable);
            }
        };
        let status = upstream.status().as_u16();
        let headers = response_headers(&upstream);
        let retry_after_seconds = retry_after_seconds(
            upstream
                .headers()
                .get("retry-after")
                .and_then(|value| value.to_str().ok()),
        );
        if is_streaming(&body) {
            return Ok(self.stream_response(
                upstream,
                headers,
                StreamForwardContext {
                    acquired,
                    status,
                    retry_after_seconds,
                    started,
                    timeout,
                },
            ));
        }
        let content = match upstream.bytes().await {
            Ok(content) => content,
            Err(_) => {
                self.settle_and_release(
                    &acquired.lease,
                    failed_settlement(&acquired.lease, started.elapsed(), "proxy_transport"),
                )
                .await;
                return Err(AccountPoolProxyError::UpstreamUnavailable);
            }
        };
        let (input_tokens, output_tokens) = usage_from_content(&content);
        self.settle_and_release(
            &acquired.lease,
            SettleRequest {
                lease_id: acquired.lease.lease_id.clone(),
                success: status < 400,
                status_code: Some(status),
                input_tokens,
                output_tokens,
                cost_usd: None,
                latency_ms: Some(started.elapsed().as_secs_f64() * 1000.0),
                error_type: (status >= 400).then_some("proxy_http_status".to_string()),
                provider_error_code: (status >= 400)
                    .then(|| provider_error_code(&content))
                    .flatten(),
                retry_after_seconds,
            },
        )
        .await;
        Ok(AccountPoolProxyResponse {
            status,
            headers,
            body: AccountPoolProxyBody::Bytes(content),
        })
    }

    fn stream_response(
        &self,
        upstream: reqwest::Response,
        headers: Vec<(String, String)>,
        context: StreamForwardContext,
    ) -> AccountPoolProxyResponse {
        let (sender, receiver) = mpsc::channel(DEFAULT_ACCOUNT_POOL_STREAM_CHANNEL_CAPACITY);
        let status = context.status;
        let proxy = self.clone();
        tokio::spawn(async move {
            proxy.copy_stream(upstream, context, sender).await;
        });
        AccountPoolProxyResponse {
            status,
            headers,
            body: AccountPoolProxyBody::Stream(receiver),
        }
    }

    async fn copy_stream(
        &self,
        upstream: reqwest::Response,
        context: StreamForwardContext,
        sender: mpsc::Sender<Result<Bytes, AccountPoolProxyStreamError>>,
    ) {
        let deadline = Instant::now() + context.timeout;
        let heartbeat_interval = Duration::from_millis(
            context
                .acquired
                .lease_ttl_seconds
                .saturating_mul(500)
                .max(100),
        );
        let mut heartbeat = tokio::time::interval(heartbeat_interval);
        heartbeat.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let mut upstream = upstream.bytes_stream();
        let mut usage = (0, 0);
        let mut completed = false;
        loop {
            tokio::select! {
                _ = tokio::time::sleep_until(deadline) => break,
                _ = heartbeat.tick() => {
                    match self.scheduler.heartbeat(&context.acquired.lease.lease_id, context.acquired.lease_ttl_seconds).await {
                        Ok(true) => {}
                        Ok(false) | Err(_) => break,
                    }
                }
                next = upstream.next() => match next {
                    Some(Ok(chunk)) => {
                        usage = stream_usage_from_chunk(&chunk, usage);
                        if sender.send(Ok(chunk)).await.is_err() {
                            break;
                        }
                    }
                    Some(Err(_)) => {
                        let _ = sender.send(Err(AccountPoolProxyStreamError)).await;
                        break;
                    }
                    None => {
                        completed = true;
                        break;
                    }
                },
            }
        }
        if !completed {
            let _ = sender.send(Err(AccountPoolProxyStreamError)).await;
        }
        self.settle_and_release(
            &context.acquired.lease,
            SettleRequest {
                lease_id: context.acquired.lease.lease_id.clone(),
                success: completed && context.status < 400,
                status_code: Some(context.status),
                input_tokens: usage.0,
                output_tokens: usage.1,
                cost_usd: None,
                latency_ms: Some(context.started.elapsed().as_secs_f64() * 1000.0),
                error_type: (!completed || context.status >= 400)
                    .then_some("stream_interrupted".to_string()),
                provider_error_code: None,
                retry_after_seconds: context.retry_after_seconds,
            },
        )
        .await;
    }

    async fn settle_and_release(&self, lease: &Lease, settlement: SettleRequest) {
        let _ = self.scheduler.settle(&settlement).await;
        self.record_event(ControlPlaneEvent::Settlement {
            lease: lease.clone(),
            settlement,
        });
        let _ = self.scheduler.release(&lease.lease_id).await;
    }

    fn record_event(&self, event: ControlPlaneEvent) {
        if let Some(sender) = &self.event_sender {
            let _ = sender.try_send(event);
        }
    }

    async fn pre_authorize(
        &self,
        headers: &[(String, String)],
    ) -> Result<(), AccountPoolProxyError> {
        let Some(pre_auth) = &self.pre_auth else {
            return Ok(());
        };
        let key = header_value(headers, "x-litellm-api-key")
            .or_else(|| header_value(headers, "authorization"))
            .filter(|value| !value.is_empty())
            .ok_or(AccountPoolProxyError::InvalidApiKey)?;
        let digest: [u8; 32] = Sha256::digest(key.as_bytes()).into();
        let now = Instant::now();
        if pre_auth
            .cache
            .lock()
            .await
            .get(&digest)
            .is_some_and(|until| *until > now)
        {
            return Ok(());
        }
        let response = self
            .http
            .get(self.litellm_url("key/info")?)
            .header("authorization", key)
            .timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_PROXY_REQUEST_TIMEOUT_SECS,
            ))
            .send()
            .await
            .map_err(|_| AccountPoolProxyError::UpstreamUnavailable)?;
        let status = response.status().as_u16();
        let _ = response.bytes().await;
        if !matches!(status, 200 | 403) {
            return Err(AccountPoolProxyError::InvalidApiKey);
        }
        let mut cache = pre_auth.cache.lock().await;
        if cache.len() >= MAXIMUM_ACCOUNT_POOL_PRE_AUTH_CACHE_ENTRIES {
            cache.retain(|_, until| *until > now);
        }
        if cache.len() < MAXIMUM_ACCOUNT_POOL_PRE_AUTH_CACHE_ENTRIES {
            cache.insert(digest, now + pre_auth.ttl);
        }
        Ok(())
    }

    fn upstream_url(&self, path: &str) -> Result<Url, AccountPoolProxyError> {
        let path = path.trim_matches('/');
        if path.is_empty() {
            return Err(AccountPoolProxyError::InvalidPath);
        }
        self.litellm_url(path)
    }

    fn litellm_url(&self, path: &str) -> Result<Url, AccountPoolProxyError> {
        let mut url = self.litellm_url.clone();
        let base_path = url.path().trim_end_matches('/');
        let path = path.trim_start_matches('/');
        let prefix = if path == "key/info" { "" } else { "/v1" };
        url.set_path(&format!("{base_path}{prefix}/{path}"));
        url.set_query(None);
        Ok(url)
    }
}

#[derive(Clone)]
struct PreAuthCache {
    cache: Arc<Mutex<HashMap<[u8; 32], Instant>>>,
    ttl: Duration,
}

impl PreAuthCache {
    fn new(ttl: Duration) -> Self {
        Self {
            cache: Arc::new(Mutex::new(HashMap::new())),
            ttl,
        }
    }
}

pub struct AccountPoolProxyRequest {
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

pub struct AccountPoolProxyResponse {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: AccountPoolProxyBody,
}

pub enum AccountPoolProxyBody {
    Bytes(Bytes),
    Stream(mpsc::Receiver<Result<Bytes, AccountPoolProxyStreamError>>),
}

#[derive(Debug)]
pub struct AccountPoolProxyStreamError;

impl Display for AccountPoolProxyStreamError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("account-pool upstream stream ended unexpectedly")
    }
}

impl std::error::Error for AccountPoolProxyStreamError {}

#[derive(Debug)]
pub enum AccountPoolProxyError {
    NotReady,
    InvalidJson,
    MissingModel,
    InvalidPath,
    InvalidApiKey,
    UpstreamUnavailable,
    NoRoute { model: String, reasons: Vec<String> },
}

fn parse_request_body(body: &[u8]) -> Result<Map<String, Value>, AccountPoolProxyError> {
    serde_json::from_slice::<Value>(body)
        .map_err(|_| AccountPoolProxyError::InvalidJson)?
        .as_object()
        .cloned()
        .ok_or(AccountPoolProxyError::InvalidJson)
}

fn request_model(body: &Map<String, Value>) -> Result<String, AccountPoolProxyError> {
    body.get("model")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|model| !model.is_empty())
        .map(ToString::to_string)
        .ok_or(AccountPoolProxyError::MissingModel)
}

fn request_id(headers: &[(String, String)]) -> String {
    header_value(headers, "x-request-id")
        .filter(|value| !value.is_empty())
        .map(ToString::to_string)
        .unwrap_or_else(|| Uuid::new_v4().simple().to_string())
}

fn estimated_tokens(body: &Map<String, Value>) -> u64 {
    ["max_tokens", "max_completion_tokens"]
        .into_iter()
        .find_map(|field| body.get(field).and_then(Value::as_u64))
        .filter(|tokens| *tokens > 0)
        .unwrap_or_default()
}

fn forwarded_body(body: &Map<String, Value>, lease: &Lease, request_id: &str) -> Value {
    let mut forwarded = body.clone();
    let mut metadata = forwarded
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    metadata.insert(
        "account_pool_lease_id".to_string(),
        Value::String(lease.lease_id.clone()),
    );
    metadata.insert(
        "account_pool_request_id".to_string(),
        Value::String(request_id.to_string()),
    );
    metadata.insert(
        "account_pool_public_model".to_string(),
        Value::String(lease.public_model.clone()),
    );
    forwarded.insert("metadata".to_string(), Value::Object(metadata));
    forwarded.insert(
        "model".to_string(),
        Value::String(lease.deployment_id.clone()),
    );
    if is_streaming(body)
        && forwarded
            .get("stream_options")
            .and_then(Value::as_object)
            .and_then(|options| options.get("include_usage"))
            != Some(&Value::Bool(true))
    {
        let mut options = forwarded
            .get("stream_options")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        options.insert("include_usage".to_string(), Value::Bool(true));
        forwarded.insert("stream_options".to_string(), Value::Object(options));
    }
    Value::Object(forwarded)
}

fn is_streaming(body: &Map<String, Value>) -> bool {
    body.get("stream") == Some(&Value::Bool(true))
}

fn usage_from_content(content: &[u8]) -> (u64, u64) {
    serde_json::from_slice::<Value>(content)
        .ok()
        .and_then(|value| usage_from_value(value.get("usage")))
        .unwrap_or_default()
}

fn stream_usage_from_chunk(chunk: &[u8], previous: (u64, u64)) -> (u64, u64) {
    let Ok(chunk) = std::str::from_utf8(chunk) else {
        return previous;
    };
    chunk
        .lines()
        .filter_map(|line| line.strip_prefix("data: ").or(Some(line)))
        .filter(|payload| !payload.is_empty() && *payload != "[DONE]")
        .filter_map(|payload| serde_json::from_str::<Value>(payload).ok())
        .find_map(|value| usage_from_value(value.get("usage")))
        .filter(|usage| *usage != (0, 0))
        .unwrap_or(previous)
}

fn usage_from_value(value: Option<&Value>) -> Option<(u64, u64)> {
    let usage = value?.as_object()?;
    Some((
        usage
            .get("prompt_tokens")
            .or_else(|| usage.get("input_tokens"))
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        usage
            .get("completion_tokens")
            .or_else(|| usage.get("output_tokens"))
            .and_then(Value::as_u64)
            .unwrap_or_default(),
    ))
}

fn provider_error_code(content: &[u8]) -> Option<String> {
    let response = serde_json::from_slice::<Value>(content).ok()?;
    let error = response.get("error")?.as_object()?;
    ["code", "type"].into_iter().find_map(|field| {
        error
            .get(field)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty() && value.chars().count() <= 128)
            .map(ToString::to_string)
    })
}

fn retry_after_seconds(value: Option<&str>) -> Option<f64> {
    let value = value?.trim();
    if value.is_empty() {
        return None;
    }
    if value.bytes().all(|byte| byte.is_ascii_digit()) {
        return value
            .parse::<f64>()
            .ok()
            .map(|seconds| seconds.min(MAXIMUM_RETRY_AFTER_SECONDS));
    }
    let retry_at = parse_http_date(value).ok()?;
    let seconds = retry_at
        .duration_since(SystemTime::now())
        .unwrap_or_default()
        .as_secs_f64();
    Some(seconds.min(MAXIMUM_RETRY_AFTER_SECONDS))
}

fn response_headers(response: &reqwest::Response) -> Vec<(String, String)> {
    response
        .headers()
        .iter()
        .filter(|(name, _)| {
            !RESPONSE_HEADER_DENYLIST
                .iter()
                .any(|excluded| name.as_str().eq_ignore_ascii_case(excluded))
        })
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.to_string(), value.to_string()))
        })
        .collect()
}

fn allowed_header(name: &str) -> bool {
    REQUEST_HEADER_ALLOWLIST
        .iter()
        .any(|allowed| name.eq_ignore_ascii_case(allowed))
}

fn header_value<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(header, _)| header.eq_ignore_ascii_case(name))
        .map(|(_, value)| value.as_str())
}

fn lease_timeout(lease: &Lease) -> Result<Duration, AccountPoolProxyError> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| AccountPoolProxyError::UpstreamUnavailable)?
        .as_secs_f64();
    let remaining = lease.absolute_expires_at - now;
    if !remaining.is_finite() || remaining <= 0.0 {
        return Err(AccountPoolProxyError::UpstreamUnavailable);
    }
    Ok(Duration::from_secs_f64(remaining))
}

fn failed_settlement(lease: &Lease, elapsed: Duration, error_type: &str) -> SettleRequest {
    SettleRequest {
        lease_id: lease.lease_id.clone(),
        success: false,
        status_code: None,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: None,
        latency_ms: Some(elapsed.as_secs_f64() * 1000.0),
        error_type: Some(error_type.to_string()),
        provider_error_code: None,
        retry_after_seconds: None,
    }
}

#[cfg(test)]
mod tests {
    use super::{
        AccountPoolControlPlane, AccountPoolControlPlaneSettings, ControlPlaneEvent,
        forwarded_body, provider_error_code, retry_after_seconds, stream_usage_from_chunk,
        usage_from_content,
    };
    use crate::account_pool::types::{Lease, SettleRequest};
    use serde_json::{Value, json};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream};
    use tokio::sync::mpsc;

    fn lease() -> Lease {
        Lease {
            lease_id: "lease-1".to_string(),
            generation_id: None,
            request_id: "request-1".to_string(),
            account_id: "account-1".to_string(),
            deployment_id: "deployment-1".to_string(),
            public_model: "public-model".to_string(),
            billing_route_id: None,
            probe: false,
            expires_at: 20.0,
            absolute_expires_at: 30.0,
            settled: false,
            released: false,
        }
    }

    #[test]
    fn rewrites_the_deployment_and_preserves_stream_usage() {
        let body = json!({
            "model": "public-model",
            "stream": true,
            "metadata": {"requester": "test"},
        })
        .as_object()
        .expect("object")
        .clone();
        let forwarded = forwarded_body(&body, &lease(), "request-1");

        assert_eq!(
            forwarded["model"],
            Value::String("deployment-1".to_string())
        );
        assert_eq!(
            forwarded["stream_options"]["include_usage"],
            Value::Bool(true)
        );
        assert_eq!(
            forwarded["metadata"]["requester"],
            Value::String("test".to_string())
        );
        assert_eq!(forwarded["metadata"]["account_pool_lease_id"], "lease-1");
    }

    #[test]
    fn extracts_usage_and_safe_provider_error_codes() {
        assert_eq!(
            usage_from_content(br#"{"usage":{"prompt_tokens":12,"completion_tokens":7}}"#),
            (12, 7)
        );
        assert_eq!(
            stream_usage_from_chunk(
                br#"data: {"usage":{"input_tokens":3,"output_tokens":4}}

                "#,
                (0, 0),
            ),
            (3, 4)
        );
        assert_eq!(
            provider_error_code(br#"{"error":{"type":"quota_exceeded"}}"#).as_deref(),
            Some("quota_exceeded")
        );
    }

    #[test]
    fn accepts_numeric_retry_after_and_rejects_invalid_values() {
        assert_eq!(retry_after_seconds(Some("45")), Some(45.0));
        assert!(retry_after_seconds(Some("not-a-date")).is_none());
    }

    #[tokio::test]
    async fn reports_ordered_events_to_the_control_plane() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("binds");
        let address = listener.local_addr().expect("listener address");
        let control_plane = AccountPoolControlPlane::new(
            AccountPoolControlPlaneSettings::new(
                format!("http://{address}/internal/runtime-config"),
                "service-token".to_string(),
            )
            .expect("settings"),
        )
        .expect("control plane");
        let (sender, receiver) = mpsc::channel(2);
        let worker = tokio::spawn(control_plane.run(receiver));
        let server = tokio::spawn(async move {
            let mut requests = Vec::new();
            for _ in 0..2 {
                let (mut socket, _) = listener.accept().await.expect("accepts request");
                requests.push(read_request(&mut socket).await);
                socket
                    .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
                    .await
                    .expect("writes response");
            }
            requests
        });

        sender
            .send(ControlPlaneEvent::RequestActivity(lease()))
            .await
            .expect("queues activity");
        sender
            .send(ControlPlaneEvent::Settlement {
                lease: lease(),
                settlement: SettleRequest {
                    lease_id: "lease-1".to_string(),
                    success: true,
                    status_code: Some(200),
                    input_tokens: 2,
                    output_tokens: 3,
                    cost_usd: None,
                    latency_ms: Some(4.0),
                    error_type: None,
                    provider_error_code: None,
                    retry_after_seconds: None,
                },
            })
            .await
            .expect("queues settlement");
        drop(sender);

        worker.await.expect("worker");
        let requests = server.await.expect("server");

        assert!(requests[0].starts_with("POST /internal/request-activity HTTP/1.1"));
        assert!(requests[1].starts_with("POST /internal/settlement-event HTTP/1.1"));
        assert!(requests.iter().all(|request| {
            request
                .to_ascii_lowercase()
                .contains("x-account-pool-token: service-token")
        }));
        let (_, settlement_body) = requests[1].split_once("\r\n\r\n").expect("request body");
        let settlement: Value = serde_json::from_str(settlement_body).expect("settlement JSON");
        assert_eq!(settlement["lease"]["lease_id"], "lease-1");
        assert_eq!(settlement["settlement"]["status_code"], 200);
    }

    async fn read_request(socket: &mut TcpStream) -> String {
        let mut received = Vec::new();
        let mut buffer = [0_u8; 4096];
        let header_end = loop {
            let count = socket.read(&mut buffer).await.expect("reads request");
            assert!(count > 0, "request closed before headers");
            received.extend_from_slice(&buffer[..count]);
            if let Some(index) = received.windows(4).position(|window| window == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let content_length = std::str::from_utf8(&received[..header_end])
            .expect("request headers")
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse::<usize>().ok())
                    .flatten()
            })
            .unwrap_or_default();
        while received.len().saturating_sub(header_end) < content_length {
            let count = socket.read(&mut buffer).await.expect("reads request body");
            assert!(count > 0, "request closed before body");
            received.extend_from_slice(&buffer[..count]);
        }
        String::from_utf8(received).expect("request utf8")
    }
}
