//! 本文件读取环境变量，并通过内部令牌从 Python 控制面获取账号池运行时快照。

use std::sync::Arc;
use std::time::Duration;

use reqwest::{Client, StatusCode, Url};
use thiserror::Error;

use super::config::{RuntimeConfigSnapshot, RuntimeConfigValidationError};
use crate::constants::{
    ACCOUNT_POOL_CONFIG_URL_ENV, ACCOUNT_POOL_ENABLED_ENV, ACCOUNT_POOL_REFRESH_INTERVAL_ENV,
    ACCOUNT_POOL_TOKEN_ENV, DEFAULT_ACCOUNT_POOL_CONFIG_URL,
    DEFAULT_ACCOUNT_POOL_CONNECT_TIMEOUT_SECS, DEFAULT_ACCOUNT_POOL_REFRESH_INTERVAL_SECS,
    DEFAULT_ACCOUNT_POOL_REQUEST_TIMEOUT_SECS,
};

#[derive(Debug, Clone)]
pub struct RuntimeConfigSettings {
    endpoint: Url,
    token: Arc<str>,
    pub refresh_interval: Duration,
}

impl RuntimeConfigSettings {
    pub fn from_env() -> Result<Option<Self>, RuntimeConfigSettingsError> {
        let enabled = std::env::var(ACCOUNT_POOL_ENABLED_ENV).unwrap_or_default();
        if !parse_enabled(&enabled)? {
            return Ok(None);
        }
        let endpoint = std::env::var(ACCOUNT_POOL_CONFIG_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_ACCOUNT_POOL_CONFIG_URL.to_string());
        let token = std::env::var(ACCOUNT_POOL_TOKEN_ENV).unwrap_or_default();
        let refresh_interval = std::env::var(ACCOUNT_POOL_REFRESH_INTERVAL_ENV)
            .ok()
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| RuntimeConfigSettingsError::InvalidRefreshInterval)
            })
            .transpose()?
            .unwrap_or(DEFAULT_ACCOUNT_POOL_REFRESH_INTERVAL_SECS);
        Self::new(endpoint, token, Duration::from_secs(refresh_interval)).map(Some)
    }

    pub fn new(
        endpoint: String,
        token: String,
        refresh_interval: Duration,
    ) -> Result<Self, RuntimeConfigSettingsError> {
        if token.trim().is_empty() {
            return Err(RuntimeConfigSettingsError::MissingToken);
        }
        if refresh_interval.is_zero() {
            return Err(RuntimeConfigSettingsError::InvalidRefreshInterval);
        }
        let endpoint =
            Url::parse(endpoint.trim()).map_err(|_| RuntimeConfigSettingsError::InvalidEndpoint)?;
        if !matches!(endpoint.scheme(), "http" | "https") {
            return Err(RuntimeConfigSettingsError::InvalidEndpoint);
        }
        Ok(Self {
            endpoint,
            token: Arc::from(token.trim()),
            refresh_interval,
        })
    }
}

fn parse_enabled(raw: &str) -> Result<bool, RuntimeConfigSettingsError> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "" | "0" | "false" | "no" => Ok(false),
        "1" | "true" | "yes" => Ok(true),
        _ => Err(RuntimeConfigSettingsError::InvalidEnabledFlag),
    }
}

#[derive(Debug, Clone)]
pub(crate) struct RuntimeConfigClient {
    http: Client,
    endpoint: Url,
    token: Arc<str>,
}

impl RuntimeConfigClient {
    pub(crate) fn new(settings: &RuntimeConfigSettings) -> Result<Self, RuntimeConfigClientError> {
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_CONNECT_TIMEOUT_SECS,
            ))
            .timeout(Duration::from_secs(
                DEFAULT_ACCOUNT_POOL_REQUEST_TIMEOUT_SECS,
            ))
            .build()
            .map_err(RuntimeConfigClientError::BuildClient)?;
        Ok(Self {
            http,
            endpoint: settings.endpoint.clone(),
            token: Arc::clone(&settings.token),
        })
    }

    pub(crate) async fn fetch(&self) -> Result<RuntimeConfigSnapshot, RuntimeConfigClientError> {
        let response = self
            .http
            .get(self.endpoint.clone())
            .header("x-account-pool-token", self.token.as_ref())
            .send()
            .await
            .map_err(RuntimeConfigClientError::Request)?;
        let status = response.status();
        if !status.is_success() {
            return Err(RuntimeConfigClientError::Status(status));
        }
        let snapshot = response
            .json::<RuntimeConfigSnapshot>()
            .await
            .map_err(RuntimeConfigClientError::Decode)?;
        snapshot
            .validate()
            .map_err(RuntimeConfigClientError::InvalidSnapshot)?;
        Ok(snapshot)
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum RuntimeConfigSettingsError {
    #[error("ACCOUNT_POOL_RUST_ENABLED must be true or false")]
    InvalidEnabledFlag,
    #[error("ACCOUNT_POOL_INTERNAL_TOKEN is required when the Rust account pool is enabled")]
    MissingToken,
    #[error("ACCOUNT_POOL_RUNTIME_CONFIG_URL must be an HTTP or HTTPS URL")]
    InvalidEndpoint,
    #[error("ACCOUNT_POOL_RUNTIME_REFRESH_INTERVAL_SECONDS must be a positive integer")]
    InvalidRefreshInterval,
}

#[derive(Debug, Error)]
pub enum RuntimeConfigClientError {
    #[error("failed to build the account-pool HTTP client: {0}")]
    BuildClient(reqwest::Error),
    #[error("account-pool runtime config request failed: {0}")]
    Request(reqwest::Error),
    #[error("account-pool runtime config returned HTTP {0}")]
    Status(StatusCode),
    #[error("account-pool runtime config response was invalid JSON: {0}")]
    Decode(reqwest::Error),
    #[error(transparent)]
    InvalidSnapshot(RuntimeConfigValidationError),
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    use super::{
        RuntimeConfigClient, RuntimeConfigSettings, RuntimeConfigSettingsError, parse_enabled,
    };

    const VALID_CONFIG: &str = r#"{
        "schema_version": 1,
        "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "generated_at": "2026-08-28T12:00:00Z",
        "lease_ttl_seconds": 60,
        "maximum_lease_seconds": 600,
        "accounts": [],
        "policies": []
    }"#;

    #[test]
    fn feature_flag_is_disabled_by_default_and_rejects_unknown_values() {
        assert_eq!(parse_enabled(""), Ok(false));
        assert_eq!(parse_enabled("true"), Ok(true));
        assert_eq!(
            parse_enabled("sometimes"),
            Err(RuntimeConfigSettingsError::InvalidEnabledFlag)
        );
    }

    #[test]
    fn enabled_settings_require_a_token_and_positive_interval() {
        assert_eq!(
            RuntimeConfigSettings::new(
                "http://127.0.0.1:4100/internal/runtime-config".to_string(),
                " ".to_string(),
                Duration::from_secs(5),
            )
            .expect_err("missing token"),
            RuntimeConfigSettingsError::MissingToken
        );
        assert_eq!(
            RuntimeConfigSettings::new(
                "http://127.0.0.1:4100/internal/runtime-config".to_string(),
                "token".to_string(),
                Duration::ZERO,
            )
            .expect_err("zero interval"),
            RuntimeConfigSettingsError::InvalidRefreshInterval
        );
    }

    #[tokio::test]
    async fn client_sends_service_token_and_validates_the_snapshot() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("binds");
        let address = listener.local_addr().expect("listener address");
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.expect("accepts request");
            let mut request = vec![0_u8; 4096];
            let count = socket.read(&mut request).await.expect("reads request");
            let request = String::from_utf8(request[..count].to_vec()).expect("utf8 request");
            assert!(request.starts_with("GET /internal/runtime-config HTTP/1.1"));
            assert!(request.contains("x-account-pool-token: service-token"));
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                VALID_CONFIG.len(),
                VALID_CONFIG
            );
            socket
                .write_all(response.as_bytes())
                .await
                .expect("writes response");
        });
        let settings = RuntimeConfigSettings::new(
            format!("http://{address}/internal/runtime-config"),
            "service-token".to_string(),
            Duration::from_secs(5),
        )
        .expect("valid settings");
        let client = RuntimeConfigClient::new(&settings).expect("client");

        let snapshot = client.fetch().await.expect("valid snapshot");

        assert_eq!(snapshot.schema_version, 1);
        server.await.expect("server task");
    }
}
