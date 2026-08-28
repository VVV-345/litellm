//! Account-pool control-plane snapshot loading and last-known-good runtime cache.

pub mod config;
pub mod types;

#[cfg(feature = "server")]
pub mod proxy;

mod client;
mod health;
mod quota;
mod scheduler;
mod scripts;
mod store;

#[cfg(feature = "server")]
pub use proxy::{AccountPoolProxyRuntime, AccountPoolProxySettings};
pub use scheduler::{AccountPoolScheduler, AccountPoolSchedulerError};
pub use store::{RedisLeaseStore, RedisStoreError};

use std::sync::Arc;

use client::RuntimeConfigClient;
pub use client::{RuntimeConfigClientError, RuntimeConfigSettings, RuntimeConfigSettingsError};
pub use config::RuntimeConfigSnapshot;
use tokio::sync::RwLock;

#[derive(Debug, Clone)]
pub struct AccountPoolRuntime {
    enabled: bool,
    state: Arc<RwLock<RuntimeState>>,
}

#[derive(Debug, Default)]
struct RuntimeState {
    snapshot: Option<Arc<RuntimeConfigSnapshot>>,
    last_error: Option<Arc<str>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccountPoolRuntimeStatus {
    pub enabled: bool,
    pub ready: bool,
    pub revision: Option<String>,
    pub last_error: Option<String>,
}

impl AccountPoolRuntime {
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            state: Arc::new(RwLock::new(RuntimeState::default())),
        }
    }

    pub fn failed(message: String) -> Self {
        Self {
            enabled: true,
            state: Arc::new(RwLock::new(RuntimeState {
                snapshot: None,
                last_error: Some(Arc::from(message)),
            })),
        }
    }

    pub async fn start(settings: RuntimeConfigSettings) -> Result<Self, RuntimeConfigClientError> {
        let client = RuntimeConfigClient::new(&settings)?;
        let runtime = Self {
            enabled: true,
            state: Arc::new(RwLock::new(RuntimeState::default())),
        };
        runtime.refresh(&client).await;

        let background_runtime = runtime.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(settings.refresh_interval).await;
                background_runtime.refresh(&client).await;
            }
        });
        Ok(runtime)
    }

    pub async fn snapshot(&self) -> Option<Arc<RuntimeConfigSnapshot>> {
        self.state.read().await.snapshot.clone()
    }

    pub async fn status(&self) -> AccountPoolRuntimeStatus {
        let state = self.state.read().await;
        AccountPoolRuntimeStatus {
            enabled: self.enabled,
            ready: !self.enabled || state.snapshot.is_some(),
            revision: state
                .snapshot
                .as_ref()
                .map(|snapshot| snapshot.revision.clone()),
            last_error: state.last_error.as_ref().map(ToString::to_string),
        }
    }

    async fn refresh(&self, client: &RuntimeConfigClient) {
        match client.fetch().await {
            Ok(snapshot) => self.store(snapshot).await,
            Err(error) => self.record_error(error.to_string()).await,
        }
    }

    async fn store(&self, snapshot: RuntimeConfigSnapshot) {
        let mut state = self.state.write().await;
        if state
            .snapshot
            .as_ref()
            .is_none_or(|current| current.revision != snapshot.revision)
        {
            state.snapshot = Some(Arc::new(snapshot));
        }
        state.last_error = None;
    }

    async fn record_error(&self, message: String) {
        self.state.write().await.last_error = Some(Arc::from(message));
    }
}

#[cfg(test)]
mod tests {
    use super::AccountPoolRuntime;
    use super::config::RuntimeConfigSnapshot;

    const VALID_CONFIG: &str = r#"{
        "schema_version": 1,
        "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "generated_at": "2026-08-28T12:00:00Z",
        "lease_ttl_seconds": 60,
        "maximum_lease_seconds": 600,
        "accounts": [],
        "policies": []
    }"#;

    #[tokio::test]
    async fn disabled_runtime_is_ready_without_a_snapshot() {
        let runtime = AccountPoolRuntime::disabled();

        assert!(runtime.status().await.ready);
        assert!(runtime.snapshot().await.is_none());
    }

    #[tokio::test]
    async fn refresh_failures_preserve_the_last_known_good_snapshot() {
        let runtime = AccountPoolRuntime::failed("initial failure".to_string());
        let snapshot: RuntimeConfigSnapshot =
            serde_json::from_str(VALID_CONFIG).expect("valid snapshot");
        runtime.store(snapshot).await;
        runtime.record_error("refresh failure".to_string()).await;

        let status = runtime.status().await;
        assert!(status.ready);
        assert_eq!(
            status.revision.as_deref(),
            Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        );
        assert_eq!(status.last_error.as_deref(), Some("refresh failure"));
    }
}
