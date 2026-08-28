//! Typed, versioned account-pool runtime configuration consumed from Python.

use std::collections::HashSet;

use serde::Deserialize;
use thiserror::Error;

pub const SCHEMA_VERSION: u16 = 1;

#[derive(Debug, Clone, PartialEq)]
pub struct DecimalValue {
    raw: String,
    parsed: f64,
}

impl DecimalValue {
    pub fn as_str(&self) -> &str {
        &self.raw
    }

    pub fn as_f64(&self) -> f64 {
        self.parsed
    }
}

impl<'de> Deserialize<'de> for DecimalValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Representation {
            String(String),
            Number(serde_json::Number),
        }

        let raw = match Representation::deserialize(deserializer)? {
            Representation::String(value) => value,
            Representation::Number(value) => value.to_string(),
        };
        let parsed = raw.parse::<f64>().map_err(serde::de::Error::custom)?;
        if !parsed.is_finite() || parsed < 0.0 {
            return Err(serde::de::Error::custom(
                "decimal values must be finite and non-negative",
            ));
        }
        Ok(Self { raw, parsed })
    }
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Strategy {
    Priority,
    Random,
    LowestLatency,
    HighestRemainingQuota,
    LowestEffectiveCost,
    LeastInflight,
    WeightedRoundRobin,
    QuotaAwareLeastInflight,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum QuotaUnit {
    Tokens,
    Usd,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeQuotaScope {
    Channel,
    Model,
    BillingRoute,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeQuotaKind {
    Requests,
    Tokens,
    Credits,
    Currency,
    ProviderUnits,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeQuotaWindowType {
    Rolling,
    Fixed,
    ResetAt,
    Lifetime,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CostEvidenceKind {
    NormalizedPerMillionTokens,
    EffectivePrices,
    SubscriptionIncluded,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeBillingMode {
    Subscription,
    Metered,
    ProviderDecided,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeQuotaConfig {
    pub unit: QuotaUnit,
    pub total: Option<f64>,
    pub five_hour: Option<f64>,
    pub weekly: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeCostEvidence {
    pub kind: CostEvidenceKind,
    pub currency: Option<String>,
    pub unit: Option<String>,
    pub input_price: Option<DecimalValue>,
    pub output_price: Option<DecimalValue>,
    pub cache_read_price: Option<DecimalValue>,
    pub cache_write_price: Option<DecimalValue>,
    pub effective_cost: DecimalValue,
    pub partial: bool,
    pub provider_group_id: Option<String>,
    pub billing_mode: RuntimeBillingMode,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeQuotaWindow {
    pub window_id: String,
    pub scope: RuntimeQuotaScope,
    pub subject_id: Option<String>,
    pub kind: RuntimeQuotaKind,
    pub window_type: Option<RuntimeQuotaWindowType>,
    pub duration_seconds: Option<u64>,
    pub limit: Option<DecimalValue>,
    pub remaining: Option<DecimalValue>,
    pub safety_reserve: DecimalValue,
    pub reset_at: Option<f64>,
    pub observed_at: f64,
    pub source: String,
    pub reason_code: String,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeDeployment {
    pub public_model: String,
    pub litellm_model_id: String,
    pub binding_id: Option<String>,
    pub billing_route_id: Option<String>,
    pub billing_mode: RuntimeBillingMode,
    pub cost_evidence: Option<RuntimeCostEvidence>,
    pub manual_order: Option<u64>,
    pub routing_weight: Option<u16>,
    pub routing_paused: bool,
    pub enabled: bool,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeAccount {
    pub id: String,
    pub channel_id: Option<String>,
    pub enabled: bool,
    pub max_concurrency: u32,
    pub priority: i32,
    pub weight: u16,
    pub quotas: RuntimeQuotaConfig,
    pub quota_windows: Vec<RuntimeQuotaWindow>,
    pub deployments: Vec<RuntimeDeployment>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeModelPolicy {
    pub model: String,
    pub strategy: Strategy,
    pub version: u64,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeConfigSnapshot {
    pub schema_version: u16,
    pub revision: String,
    pub generated_at: String,
    pub lease_ttl_seconds: u64,
    pub maximum_lease_seconds: u64,
    pub accounts: Vec<RuntimeAccount>,
    pub policies: Vec<RuntimeModelPolicy>,
}

impl RuntimeConfigSnapshot {
    pub fn validate(&self) -> Result<(), RuntimeConfigValidationError> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(RuntimeConfigValidationError::UnsupportedSchemaVersion(
                self.schema_version,
            ));
        }
        if self.revision.len() != 64 || !self.revision.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(RuntimeConfigValidationError::InvalidField("revision"));
        }
        if self.generated_at.trim().is_empty() {
            return Err(RuntimeConfigValidationError::InvalidField("generated_at"));
        }
        if self.lease_ttl_seconds == 0 || self.maximum_lease_seconds < self.lease_ttl_seconds {
            return Err(RuntimeConfigValidationError::InvalidField("lease duration"));
        }

        let mut account_ids = HashSet::new();
        let mut deployment_ids = HashSet::new();
        let mut quota_window_ids = HashSet::new();
        for account in &self.accounts {
            validate_account(account)?;
            if !account_ids.insert(account.id.as_str()) {
                return Err(RuntimeConfigValidationError::DuplicateId("account"));
            }
            for deployment in &account.deployments {
                if !deployment_ids.insert(deployment.litellm_model_id.as_str()) {
                    return Err(RuntimeConfigValidationError::DuplicateId("deployment"));
                }
            }
            for window in &account.quota_windows {
                if !quota_window_ids.insert(window.window_id.as_str()) {
                    return Err(RuntimeConfigValidationError::DuplicateId("quota window"));
                }
            }
        }

        let mut policy_models = HashSet::new();
        for policy in &self.policies {
            if policy.model.trim().is_empty() {
                return Err(RuntimeConfigValidationError::InvalidField("policy model"));
            }
            if !policy_models.insert(policy.model.as_str()) {
                return Err(RuntimeConfigValidationError::DuplicateId("policy model"));
            }
        }
        Ok(())
    }
}

fn validate_account(account: &RuntimeAccount) -> Result<(), RuntimeConfigValidationError> {
    if account.id.trim().is_empty()
        || account.max_concurrency == 0
        || account.weight == 0
        || !valid_quota_config(&account.quotas)
    {
        return Err(RuntimeConfigValidationError::InvalidField("account"));
    }
    if account.deployments.is_empty() {
        return Err(RuntimeConfigValidationError::InvalidField(
            "account deployments",
        ));
    }
    for deployment in &account.deployments {
        if deployment.public_model.trim().is_empty()
            || deployment.litellm_model_id.trim().is_empty()
            || deployment
                .billing_route_id
                .as_deref()
                .is_some_and(|route| route.trim().is_empty())
            || deployment.routing_weight == Some(0)
            || deployment.routing_weight.is_some_and(|weight| weight > 100)
            || !valid_cost_evidence(deployment.cost_evidence.as_ref())
        {
            return Err(RuntimeConfigValidationError::InvalidField("deployment"));
        }
    }
    for window in &account.quota_windows {
        validate_quota_window(window)?;
    }
    Ok(())
}

fn valid_quota_config(config: &RuntimeQuotaConfig) -> bool {
    [config.total, config.five_hour, config.weekly]
        .into_iter()
        .flatten()
        .all(|value| value.is_finite() && value >= 0.0)
}

fn valid_cost_evidence(evidence: Option<&RuntimeCostEvidence>) -> bool {
    let Some(evidence) = evidence else {
        return true;
    };
    let fields = [
        evidence.currency.as_deref(),
        evidence.unit.as_deref(),
        evidence.provider_group_id.as_deref(),
    ];
    if fields
        .into_iter()
        .flatten()
        .any(|value| value.trim().is_empty())
    {
        return false;
    }
    if evidence.kind == CostEvidenceKind::SubscriptionIncluded {
        return evidence.effective_cost.as_f64() == 0.0
            && evidence.currency.is_none()
            && evidence.unit.is_none();
    }
    evidence.currency.is_some()
        && evidence.unit.is_some()
        && (evidence.input_price.is_some() || evidence.output_price.is_some())
}

fn validate_quota_window(window: &RuntimeQuotaWindow) -> Result<(), RuntimeConfigValidationError> {
    let channel_scope_is_valid =
        matches!(window.scope, RuntimeQuotaScope::Channel) == window.subject_id.is_none();
    let rolling_is_valid = !matches!(window.window_type, Some(RuntimeQuotaWindowType::Rolling))
        || window.duration_seconds.is_some_and(|duration| duration > 0);
    let reset_is_valid = !matches!(window.window_type, Some(RuntimeQuotaWindowType::ResetAt))
        || window
            .reset_at
            .is_some_and(|reset| reset.is_finite() && reset >= 0.0);
    let reserve_is_valid = window
        .limit
        .as_ref()
        .is_none_or(|limit| window.safety_reserve.as_f64() <= limit.as_f64());
    if window.window_id.trim().is_empty()
        || window
            .subject_id
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        || window.source.trim().is_empty()
        || window.reason_code.trim().is_empty()
        || !window.observed_at.is_finite()
        || window.observed_at < 0.0
        || window.duration_seconds == Some(0)
        || !channel_scope_is_valid
        || !rolling_is_valid
        || !reset_is_valid
        || !reserve_is_valid
    {
        return Err(RuntimeConfigValidationError::InvalidField("quota window"));
    }
    Ok(())
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum RuntimeConfigValidationError {
    #[error("unsupported account-pool runtime schema version {0}")]
    UnsupportedSchemaVersion(u16),
    #[error("invalid account-pool runtime field: {0}")]
    InvalidField(&'static str),
    #[error("duplicate account-pool runtime {0} id")]
    DuplicateId(&'static str),
}

#[cfg(test)]
mod tests {
    use super::{RuntimeConfigSnapshot, RuntimeConfigValidationError, SCHEMA_VERSION};

    const VALID_CONFIG: &str = r#"{
        "schema_version": 1,
        "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "generated_at": "2026-08-28T12:00:00Z",
        "lease_ttl_seconds": 60,
        "maximum_lease_seconds": 600,
        "accounts": [{
            "id": "primary",
            "channel_id": null,
            "enabled": true,
            "max_concurrency": 4,
            "priority": 400,
            "weight": 3,
            "quotas": {"unit": "tokens", "total": 1000.0, "five_hour": null, "weekly": null},
            "quota_windows": [{
                "window_id": "channel:primary:tokens",
                "scope": "channel",
                "subject_id": null,
                "kind": "tokens",
                "window_type": "rolling",
                "duration_seconds": 3600,
                "limit": "1000",
                "remaining": "800.5",
                "safety_reserve": "0",
                "reset_at": null,
                "observed_at": 1777000000.0,
                "source": "provider",
                "reason_code": "provider_quota"
            }],
            "deployments": [{
                "public_model": "gpt-test",
                "litellm_model_id": "deployment-primary",
                "binding_id": null,
                "billing_route_id": null,
                "billing_mode": "provider_decided",
                "cost_evidence": null,
                "manual_order": null,
                "routing_weight": null,
                "routing_paused": false,
                "enabled": true
            }]
        }],
        "policies": [{"model": "gpt-test", "strategy": "least_inflight", "version": 2}]
    }"#;

    #[test]
    fn deserializes_and_validates_python_contract() {
        let snapshot: RuntimeConfigSnapshot =
            serde_json::from_str(VALID_CONFIG).expect("valid JSON contract");

        assert_eq!(snapshot.schema_version, SCHEMA_VERSION);
        assert_eq!(snapshot.accounts[0].id, "primary");
        assert_eq!(
            snapshot.accounts[0].quota_windows[0]
                .remaining
                .as_ref()
                .expect("remaining quota")
                .as_str(),
            "800.5"
        );
        assert_eq!(snapshot.validate(), Ok(()));
    }

    #[test]
    fn rejects_unknown_fields_and_duplicate_deployments() {
        let with_secret = VALID_CONFIG.replace(
            "\"enabled\": true,\n            \"max_concurrency\"",
            "\"enabled\": true,\n            \"api_key\": \"secret\",\n            \"max_concurrency\"",
        );
        assert!(serde_json::from_str::<RuntimeConfigSnapshot>(&with_secret).is_err());

        let mut snapshot: RuntimeConfigSnapshot =
            serde_json::from_str(VALID_CONFIG).expect("valid JSON contract");
        let mut duplicate = snapshot.accounts[0].clone();
        duplicate.id = "backup".to_string();
        snapshot.accounts.push(duplicate);
        assert_eq!(
            snapshot.validate(),
            Err(RuntimeConfigValidationError::DuplicateId("deployment"))
        );
    }

    #[test]
    fn rejects_unsupported_schema_versions() {
        let mut snapshot: RuntimeConfigSnapshot =
            serde_json::from_str(VALID_CONFIG).expect("valid JSON contract");
        snapshot.schema_version = 2;

        assert_eq!(
            snapshot.validate(),
            Err(RuntimeConfigValidationError::UnsupportedSchemaVersion(2))
        );
    }

    #[test]
    fn rejects_runtime_values_outside_the_python_contract() {
        let mut snapshot: RuntimeConfigSnapshot =
            serde_json::from_str(VALID_CONFIG).expect("valid JSON contract");
        snapshot.accounts[0].quotas.total = Some(-1.0);

        assert_eq!(
            snapshot.validate(),
            Err(RuntimeConfigValidationError::InvalidField("account"))
        );
    }
}
