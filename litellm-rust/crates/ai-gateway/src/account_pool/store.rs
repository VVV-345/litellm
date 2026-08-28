//! Redis-backed account state, atomic quota reservation, and lease lifecycle.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use futures_util::future::try_join_all;
use redis::aio::MultiplexedConnection;
use redis::{AsyncCommands, Script};
use thiserror::Error;
use uuid::Uuid;

use super::config::{RuntimeAccount, RuntimeDeployment};
use super::health::classify_settlement;
use super::quota::{DECIMAL_SCALE, reservation_plan, settlement_units};
use super::scripts::{HEARTBEAT, RELEASE, RESERVE, SETTLE};
use super::types::{AccountSnapshot, Health, Lease, QuotaSnapshot, SettleRequest};

const ACCOUNT_PREFIX: &str = "pool:account:";
const LEASE_EXPIRIES_KEY: &str = "pool:leases:expiries";
const QUOTA_GENERATION_KEY: &str = "pool:quota:generation";
const RELEASED_LEASE_RETENTION_SECONDS: u64 = 600;
const LATENCY_EWMA_ALPHA: f64 = 0.2;

#[derive(Clone)]
pub struct RedisLeaseStore {
    connection: MultiplexedConnection,
    reserve_script: Script,
    release_script: Script,
    heartbeat_script: Script,
    settle_script: Script,
}

pub(crate) struct ReserveRequest<'a> {
    pub account: &'a RuntimeAccount,
    pub deployment: &'a RuntimeDeployment,
    pub public_model: &'a str,
    pub request_id: &'a str,
    pub estimated_tokens: u64,
    pub lease_ttl_seconds: u64,
    pub maximum_lease_seconds: u64,
}

impl RedisLeaseStore {
    pub async fn connect(url: &str) -> Result<Self, RedisStoreError> {
        let client = redis::Client::open(url).map_err(RedisStoreError::Connect)?;
        let connection = client
            .get_multiplexed_async_connection()
            .await
            .map_err(RedisStoreError::Connect)?;
        Ok(Self {
            connection,
            reserve_script: Script::new(RESERVE.as_str()),
            release_script: Script::new(RELEASE.as_str()),
            heartbeat_script: Script::new(HEARTBEAT),
            settle_script: Script::new(SETTLE.as_str()),
        })
    }

    pub async fn account_snapshot(
        &self,
        account: &RuntimeAccount,
    ) -> Result<AccountSnapshot, RedisStoreError> {
        let mut connection = self.connection.clone();
        let state: HashMap<String, String> = connection
            .hgetall(state_key(&account.id))
            .await
            .map_err(RedisStoreError::Command)?;
        let inflight: Option<u32> = connection
            .get(inflight_key(&account.id))
            .await
            .map_err(RedisStoreError::Command)?;
        let cooldown_until = parse_optional_f64(state.get("cooldown_until"))?;
        Ok(AccountSnapshot {
            account_id: account.id.clone(),
            enabled: state.get("enabled").is_some_and(|value| value == "1"),
            health: Health::parse(state.get("health").map(String::as_str))
                .ok_or(RedisStoreError::InvalidState)?,
            inflight: inflight.unwrap_or_default(),
            max_concurrency: parse_or(state.get("max_concurrency"), account.max_concurrency)?,
            cooldown_until: cooldown_until.filter(|value| *value > 0.0),
            consecutive_failures: parse_or(state.get("consecutive_failures"), 0)?,
            reason_code: state
                .get("reason_code")
                .filter(|value| !value.is_empty())
                .cloned(),
            quota: QuotaSnapshot {
                total: parse_optional_f64(state.get("quota_total"))?,
                five_hour: parse_optional_f64(state.get("quota_five_hour"))?,
                weekly: parse_optional_f64(state.get("quota_weekly"))?,
            },
        })
    }

    pub async fn active_exclusion_reason(
        &self,
        account_id: &str,
        public_model: &str,
        deployment: &RuntimeDeployment,
        now: f64,
    ) -> Result<Option<String>, RedisStoreError> {
        let keys = eligibility_keys(account_id, public_model, deployment);
        let entries = try_join_all(keys.iter().map(|key| {
            let mut connection = self.connection.clone();
            async move {
                connection
                    .hgetall::<_, HashMap<String, String>>(key)
                    .await
                    .map_err(RedisStoreError::Command)
            }
        }))
        .await?;
        let active = entries
            .iter()
            .enumerate()
            .flat_map(|(scope_rank, entries)| {
                entries.iter().filter_map(move |(field, value)| {
                    parse_active_eligibility(field, value, now)
                        .transpose()
                        .map(|entry| entry.map(|entry| (scope_rank, entry)))
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(select_active_reason(active))
    }

    pub async fn latency(&self, deployment_id: &str) -> Result<Option<f64>, RedisStoreError> {
        let mut connection = self.connection.clone();
        let value: Option<String> = connection
            .hget(latency_key(deployment_id), "ewma_ms")
            .await
            .map_err(RedisStoreError::Command)?;
        parse_optional_f64(value.as_ref())
    }

    pub async fn next_sequence(&self, model: &str) -> Result<u64, RedisStoreError> {
        let mut connection = self.connection.clone();
        connection
            .incr(format!("pool:model:{model}:sequence"), 1_u8)
            .await
            .map_err(RedisStoreError::Command)
    }

    pub(crate) async fn reserve(
        &self,
        request: ReserveRequest<'_>,
    ) -> Result<ReserveOutcome, RedisStoreError> {
        let ReserveRequest {
            account,
            deployment,
            public_model,
            request_id,
            estimated_tokens,
            lease_ttl_seconds,
            maximum_lease_seconds,
        } = request;
        let reservations = reservation_plan(
            account,
            public_model,
            deployment.billing_route_id.as_deref(),
            estimated_tokens,
        )
        .map_err(|error| RedisStoreError::InvalidQuota(error.to_string()))?;
        let lease_id = Uuid::new_v4().simple().to_string();
        let now = unix_timestamp()?;
        let absolute_expires_at = now + maximum_lease_seconds as f64;
        let expires_at = (now + lease_ttl_seconds as f64).min(absolute_expires_at);
        let retention = (lease_ttl_seconds * 10).max(RELEASED_LEASE_RETENTION_SECONDS);
        let mut connection = self.connection.clone();
        let generation: Option<String> = connection
            .get(QUOTA_GENERATION_KEY)
            .await
            .map_err(RedisStoreError::Command)?;
        let mut invocation = self.reserve_script.prepare_invoke();
        invocation
            .key(state_key(&account.id))
            .key(inflight_key(&account.id))
            .key(lease_key(&lease_id))
            .key(format!("pool:request:{request_id}"))
            .key(LEASE_EXPIRIES_KEY);
        for key in eligibility_keys(&account.id, public_model, deployment) {
            invocation.key(key);
        }
        for reservation in &reservations {
            invocation
                .key(&reservation.window_key)
                .key(&reservation.usage_key);
        }
        invocation.key(QUOTA_GENERATION_KEY);
        invocation
            .arg(&lease_id)
            .arg(request_id)
            .arg(&account.id)
            .arg(&deployment.litellm_model_id)
            .arg(public_model)
            .arg(deployment.billing_route_id.as_deref().unwrap_or_default())
            .arg(expires_at)
            .arg(now)
            .arg(retention)
            .arg(lease_ttl_seconds)
            .arg(reservations.len())
            .arg(DECIMAL_SCALE);
        for reservation in &reservations {
            invocation.arg(&reservation.amount_units);
        }
        invocation
            .arg(generation.as_deref().unwrap_or_default())
            .arg(0_u8)
            .arg(absolute_expires_at);
        let (status, actual_lease_id, reason): (i64, String, String) = invocation
            .invoke_async(&mut connection)
            .await
            .map_err(RedisStoreError::Command)?;
        if status == 0 {
            return Ok(ReserveOutcome::Rejected(reason));
        }
        let lease = self
            .read_lease(&actual_lease_id)
            .await?
            .ok_or(RedisStoreError::LeaseNotFound)?;
        Ok(ReserveOutcome::Reserved(lease))
    }

    pub async fn read_lease(&self, lease_id: &str) -> Result<Option<Lease>, RedisStoreError> {
        let mut connection = self.connection.clone();
        let values: HashMap<String, String> = connection
            .hgetall(lease_key(lease_id))
            .await
            .map_err(RedisStoreError::Command)?;
        if values.is_empty() {
            return Ok(None);
        }
        Ok(Some(Lease {
            lease_id: required(&values, "lease_id")?.to_string(),
            generation_id: values
                .get("generation_id")
                .filter(|value| !value.is_empty())
                .cloned(),
            request_id: required(&values, "request_id")?.to_string(),
            account_id: required(&values, "account_id")?.to_string(),
            deployment_id: required(&values, "deployment_id")?.to_string(),
            public_model: required(&values, "public_model")?.to_string(),
            billing_route_id: values
                .get("billing_route_id")
                .filter(|value| !value.is_empty())
                .cloned(),
            probe: values.get("probe").is_some_and(|value| value == "1"),
            expires_at: required(&values, "expires_at").and_then(parse_f64_field)?,
            absolute_expires_at: required(&values, "absolute_expires_at")
                .and_then(parse_f64_field)?,
            settled: values.get("settled").is_some_and(|value| value == "1"),
            released: values.get("released").is_some_and(|value| value == "1"),
        }))
    }

    pub async fn heartbeat(
        &self,
        lease_id: &str,
        ttl_seconds: u64,
    ) -> Result<bool, RedisStoreError> {
        let now = unix_timestamp()?;
        let mut connection = self.connection.clone();
        let result: i64 = self
            .heartbeat_script
            .key(lease_key(lease_id))
            .key(LEASE_EXPIRIES_KEY)
            .key(QUOTA_GENERATION_KEY)
            .arg(now + ttl_seconds as f64)
            .arg(lease_id)
            .arg(ttl_seconds)
            .arg(now)
            .invoke_async(&mut connection)
            .await
            .map_err(RedisStoreError::Command)?;
        Ok(result > 0)
    }

    pub async fn release(&self, lease_id: &str) -> Result<bool, RedisStoreError> {
        let now = unix_timestamp()?;
        let mut connection = self.connection.clone();
        let result: i64 = self
            .release_script
            .key(lease_key(lease_id))
            .key(LEASE_EXPIRIES_KEY)
            .key(QUOTA_GENERATION_KEY)
            .arg(ACCOUNT_PREFIX)
            .arg(lease_id)
            .arg(RELEASED_LEASE_RETENTION_SECONDS)
            .arg(now)
            .arg(DECIMAL_SCALE)
            .invoke_async(&mut connection)
            .await
            .map_err(RedisStoreError::Command)?;
        Ok(result > 0)
    }

    pub async fn settle(&self, request: &SettleRequest) -> Result<bool, RedisStoreError> {
        let Some(lease) = self.read_lease(&request.lease_id).await? else {
            return Ok(false);
        };
        let now = unix_timestamp()?;
        let transition = classify_settlement(request, &lease, now);
        let (request_units, token_units, currency_units) = settlement_units(
            request.input_tokens,
            request.output_tokens,
            request.cost_usd.as_deref(),
        )
        .map_err(|error| RedisStoreError::InvalidQuota(error.to_string()))?;
        let legacy_consumption = self.legacy_consumption(&lease, request).await?;
        let keys = eligibility_keys(
            &lease.account_id,
            &lease.public_model,
            &RuntimeDeployment {
                public_model: lease.public_model.clone(),
                litellm_model_id: lease.deployment_id.clone(),
                binding_id: None,
                billing_route_id: lease.billing_route_id.clone(),
                billing_mode: super::config::RuntimeBillingMode::ProviderDecided,
                cost_evidence: None,
                manual_order: None,
                routing_weight: None,
                routing_paused: false,
                enabled: true,
            },
        );
        let mut connection = self.connection.clone();
        let result: i64 = self
            .settle_script
            .key(lease_key(&request.lease_id))
            .key(&keys[0])
            .key(&keys[1])
            .key(&keys[2])
            .key(&keys[3])
            .key(latency_key(&lease.deployment_id))
            .key(QUOTA_GENERATION_KEY)
            .arg(ACCOUNT_PREFIX)
            .arg(transition.action)
            .arg(legacy_consumption)
            .arg(transition.cooldown_until)
            .arg(transition.reason_code)
            .arg(transition.exclusion_scope)
            .arg(transition.exclusion_source)
            .arg(now)
            .arg(transition.retry_at)
            .arg(request_units)
            .arg(token_units)
            .arg(currency_units)
            .arg(now)
            .arg(u8::from(request.success))
            .arg(DECIMAL_SCALE)
            .arg(request.latency_ms.unwrap_or_default())
            .arg(LATENCY_EWMA_ALPHA)
            .invoke_async(&mut connection)
            .await
            .map_err(RedisStoreError::Command)?;
        Ok(result > 0)
    }

    async fn legacy_consumption(
        &self,
        lease: &Lease,
        request: &SettleRequest,
    ) -> Result<f64, RedisStoreError> {
        let mut connection = self.connection.clone();
        let quota_unit: Option<String> = connection
            .hget(state_key(&lease.account_id), "quota_unit")
            .await
            .map_err(RedisStoreError::Command)?;
        match quota_unit.as_deref() {
            Some("usd") => request
                .cost_usd
                .as_deref()
                .unwrap_or("0")
                .parse::<f64>()
                .ok()
                .filter(|value| value.is_finite() && *value >= 0.0)
                .ok_or_else(|| RedisStoreError::InvalidQuota("invalid USD cost".to_string())),
            Some("tokens") => request
                .input_tokens
                .checked_add(request.output_tokens)
                .map(|tokens| tokens as f64)
                .ok_or_else(|| {
                    RedisStoreError::InvalidQuota("token total is too large".to_string())
                }),
            _ => Err(RedisStoreError::InvalidState),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum ReserveOutcome {
    Reserved(Lease),
    Rejected(String),
}

fn eligibility_keys(
    account_id: &str,
    public_model: &str,
    deployment: &RuntimeDeployment,
) -> [String; 4] {
    [
        format!("pool:eligibility:channel:{account_id}"),
        format!("pool:eligibility:model:{account_id}:{public_model}"),
        format!(
            "pool:eligibility:deployment:{account_id}:{}",
            deployment.litellm_model_id
        ),
        deployment.billing_route_id.as_ref().map_or_else(
            || "pool:eligibility:none".to_string(),
            |billing_route_id| {
                format!("pool:eligibility:billing_route:{account_id}:{billing_route_id}")
            },
        ),
    ]
}

fn parse_active_eligibility(
    field: &str,
    value: &str,
    now: f64,
) -> Result<Option<ActiveEligibility>, RedisStoreError> {
    let (source, reason) = field
        .split_once('|')
        .ok_or(RedisStoreError::InvalidEligibility)?;
    if !matches!(source, "health" | "restriction" | "capacity") || reason.is_empty() {
        return Err(RedisStoreError::InvalidEligibility);
    }
    let (starts_at, retry_at) = value
        .split_once('|')
        .ok_or(RedisStoreError::InvalidEligibility)?;
    let starts_at = starts_at
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite() && *value >= 0.0)
        .ok_or(RedisStoreError::InvalidEligibility)?;
    let retry_at = retry_at
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite() && *value >= 0.0)
        .ok_or(RedisStoreError::InvalidEligibility)?;
    if retry_at > 0.0 && retry_at < starts_at {
        return Err(RedisStoreError::InvalidEligibility);
    }
    Ok(
        (retry_at == 0.0 || retry_at > now).then_some(ActiveEligibility {
            starts_at,
            reason: reason.to_string(),
        }),
    )
}

struct ActiveEligibility {
    starts_at: f64,
    reason: String,
}

fn select_active_reason(entries: Vec<(usize, ActiveEligibility)>) -> Option<String> {
    entries
        .into_iter()
        .min_by(|(left_scope, left), (right_scope, right)| {
            left_scope
                .cmp(right_scope)
                .then_with(|| left.starts_at.total_cmp(&right.starts_at))
                .then_with(|| left.reason.cmp(&right.reason))
        })
        .map(|(_, entry)| entry.reason)
}

fn state_key(account_id: &str) -> String {
    format!("{ACCOUNT_PREFIX}{account_id}:state")
}

fn inflight_key(account_id: &str) -> String {
    format!("{ACCOUNT_PREFIX}{account_id}:inflight")
}

fn lease_key(lease_id: &str) -> String {
    format!("pool:lease:{lease_id}")
}

fn latency_key(deployment_id: &str) -> String {
    format!("pool:latency:{deployment_id}")
}

pub(crate) fn unix_timestamp() -> Result<f64, RedisStoreError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .map_err(|_| RedisStoreError::Clock)
}

fn parse_optional_f64(value: Option<&String>) -> Result<Option<f64>, RedisStoreError> {
    value
        .filter(|value| !value.is_empty())
        .map(|value| parse_f64_field(value))
        .transpose()
}

fn parse_f64_field(value: &str) -> Result<f64, RedisStoreError> {
    value.parse().map_err(|_| RedisStoreError::InvalidState)
}

fn parse_or<T>(value: Option<&String>, fallback: T) -> Result<T, RedisStoreError>
where
    T: std::str::FromStr,
{
    value
        .filter(|value| !value.is_empty())
        .map(|value| value.parse().map_err(|_| RedisStoreError::InvalidState))
        .transpose()
        .map(|value| value.unwrap_or(fallback))
}

fn required<'a>(
    values: &'a HashMap<String, String>,
    field: &'static str,
) -> Result<&'a str, RedisStoreError> {
    values
        .get(field)
        .filter(|value| !value.is_empty())
        .map(String::as_str)
        .ok_or(RedisStoreError::MissingLeaseField(field))
}

#[derive(Debug, Error)]
pub enum RedisStoreError {
    #[error("failed to connect to account-pool Redis: {0}")]
    Connect(redis::RedisError),
    #[error("account-pool Redis command failed: {0}")]
    Command(redis::RedisError),
    #[error("account-pool Redis state is invalid")]
    InvalidState,
    #[error("account-pool Redis eligibility state is invalid")]
    InvalidEligibility,
    #[error("account-pool quota configuration is invalid: {0}")]
    InvalidQuota(String),
    #[error("account-pool lease is missing field {0}")]
    MissingLeaseField(&'static str),
    #[error("account-pool lease was not found after reservation")]
    LeaseNotFound,
    #[error("system clock is before the Unix epoch")]
    Clock,
}

#[cfg(test)]
mod tests {
    use super::{
        ActiveEligibility, RedisStoreError, parse_active_eligibility, select_active_reason,
    };

    #[test]
    fn parses_active_eligibility_and_ignores_expired_evidence() {
        let active = parse_active_eligibility("health|rate_limited", "10|20", 15.0)
            .expect("valid eligibility")
            .expect("active eligibility");
        let expired = parse_active_eligibility("health|rate_limited", "10|20", 20.0)
            .expect("valid eligibility");

        assert_eq!(active.reason, "rate_limited");
        assert!(expired.is_none());
        assert!(matches!(
            parse_active_eligibility("unknown|rate_limited", "10|20", 15.0),
            Err(RedisStoreError::InvalidEligibility)
        ));
    }

    #[test]
    fn active_eligibility_prefers_scope_then_oldest_evidence_then_reason() {
        let reason = select_active_reason(vec![
            (
                2,
                ActiveEligibility {
                    starts_at: 1.0,
                    reason: "deployment".to_string(),
                },
            ),
            (
                0,
                ActiveEligibility {
                    starts_at: 5.0,
                    reason: "channel-later".to_string(),
                },
            ),
            (
                0,
                ActiveEligibility {
                    starts_at: 2.0,
                    reason: "channel-earlier".to_string(),
                },
            ),
        ]);

        assert_eq!(reason.as_deref(), Some("channel-earlier"));
    }
}
