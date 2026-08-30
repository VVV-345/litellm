//! 本文件合并控制面快照、Redis 实时状态和纯排序逻辑，选出可预占的账号与 Deployment。

use std::collections::{BTreeSet, HashMap};

use futures_util::future::try_join_all;
use litellm_core::router::account_pool::{
    AccountPoolCandidate, AccountPoolStrategy, order_candidates,
};
use thiserror::Error;

use super::AccountPoolRuntime;
use super::config::{
    CostEvidenceKind, RuntimeAccount, RuntimeConfigSnapshot, RuntimeDeployment, Strategy,
};
use super::store::{
    RedisLeaseStore, RedisStoreError, ReserveOutcome, ReserveRequest, unix_timestamp,
};
use super::types::{
    AccountSnapshot, AcquireRequest, AcquireResult, AcquiredLease, Health, SettleRequest,
};

#[derive(Clone)]
pub struct AccountPoolScheduler {
    runtime: AccountPoolRuntime,
    store: RedisLeaseStore,
}

impl AccountPoolScheduler {
    pub fn new(runtime: AccountPoolRuntime, store: RedisLeaseStore) -> Self {
        Self { runtime, store }
    }

    pub async fn acquire(
        &self,
        request: &AcquireRequest,
    ) -> Result<AcquireResult, AccountPoolSchedulerError> {
        let snapshot = self
            .runtime
            .snapshot()
            .await
            .ok_or(AccountPoolSchedulerError::RuntimeUnavailable)?;
        let locations = candidate_locations(&snapshot, &request.model);
        if locations.is_empty() {
            return Ok(AcquireResult::Unavailable {
                model: request.model.clone(),
                reason_codes: vec!["model_not_configured".to_string()],
            });
        }

        let now = unix_timestamp()?;
        let account_states = self.account_states(&snapshot, &locations).await?;
        let observations = try_join_all(locations.iter().map(|location| {
            let account = &snapshot.accounts[location.account_index];
            let deployment = &account.deployments[location.deployment_index];
            self.candidate_observation(account, deployment, &request.model, now)
        }))
        .await?;
        let candidates =
            build_candidates(&snapshot, &locations, &account_states, observations, now)?;
        let strategy = policy_strategy(&snapshot, &request.model);
        let sequence = if strategy == Strategy::WeightedRoundRobin {
            Some(self.store.next_sequence(&request.model).await?)
        } else {
            None
        };
        let routing_candidates = candidates
            .iter()
            .map(|candidate| candidate.routing.clone())
            .collect::<Vec<_>>();
        let locations_by_id = candidates
            .iter()
            .enumerate()
            .map(|(index, candidate)| (candidate.routing.stable_id(), index))
            .collect::<HashMap<_, _>>();
        let ordered = order_candidates(
            &routing_candidates,
            strategy.into(),
            &request.model,
            Some(&request.request_id),
            sequence,
        );
        let mut reasons = Vec::new();

        // 排序只是读取时的预览，状态随后可能变化；Redis Lua 预占才是最终权威判断。
        for order in ordered {
            let candidate_index = locations_by_id
                .get(&order.candidate.stable_id())
                .copied()
                .ok_or(AccountPoolSchedulerError::CandidateMissing)?;
            let location = candidates[candidate_index].location;
            let account = &snapshot.accounts[location.account_index];
            let deployment = &account.deployments[location.deployment_index];
            if let Some(reason) = configuration_reason(deployment) {
                push_unique_reason(&mut reasons, reason);
                continue;
            }
            match self
                .store
                .reserve(ReserveRequest {
                    account,
                    deployment,
                    public_model: &request.model,
                    request_id: &request.request_id,
                    estimated_tokens: request.estimated_tokens,
                    lease_ttl_seconds: snapshot.lease_ttl_seconds,
                    maximum_lease_seconds: snapshot.maximum_lease_seconds,
                })
                .await?
            {
                ReserveOutcome::Reserved(lease) => {
                    return Ok(AcquireResult::Acquired(AcquiredLease {
                        lease,
                        lease_ttl_seconds: snapshot.lease_ttl_seconds,
                    }));
                }
                ReserveOutcome::Rejected(reason) => push_unique_reason(&mut reasons, &reason),
            }
        }

        Ok(AcquireResult::Unavailable {
            model: request.model.clone(),
            reason_codes: reasons,
        })
    }

    pub async fn heartbeat(
        &self,
        lease_id: &str,
        ttl_seconds: u64,
    ) -> Result<bool, AccountPoolSchedulerError> {
        self.store
            .heartbeat(lease_id, ttl_seconds)
            .await
            .map_err(Into::into)
    }

    pub async fn release(&self, lease_id: &str) -> Result<bool, AccountPoolSchedulerError> {
        self.store.release(lease_id).await.map_err(Into::into)
    }

    pub async fn settle(&self, request: &SettleRequest) -> Result<bool, AccountPoolSchedulerError> {
        self.store.settle(request).await.map_err(Into::into)
    }

    async fn account_states(
        &self,
        snapshot: &RuntimeConfigSnapshot,
        locations: &[CandidateLocation],
    ) -> Result<HashMap<usize, AccountSnapshot>, AccountPoolSchedulerError> {
        let account_indexes = locations
            .iter()
            .map(|location| location.account_index)
            .collect::<BTreeSet<_>>();
        let indexes = account_indexes.into_iter().collect::<Vec<_>>();
        let states = try_join_all(
            indexes
                .iter()
                .map(|index| self.store.account_snapshot(&snapshot.accounts[*index])),
        )
        .await?;
        Ok(indexes.into_iter().zip(states).collect())
    }

    async fn candidate_observation(
        &self,
        account: &RuntimeAccount,
        deployment: &RuntimeDeployment,
        model: &str,
        now: f64,
    ) -> Result<CandidateObservation, RedisStoreError> {
        let (latency_ewma_ms, active_exclusion) = tokio::try_join!(
            self.store.latency(&deployment.litellm_model_id),
            self.store
                .active_exclusion_reason(&account.id, model, deployment, now),
        )?;
        Ok(CandidateObservation {
            latency_ewma_ms,
            active_exclusion,
        })
    }
}

#[derive(Debug, Error)]
pub enum AccountPoolSchedulerError {
    #[error("account-pool runtime configuration is unavailable")]
    RuntimeUnavailable,
    #[error("account-pool ordered candidate was not found")]
    CandidateMissing,
    #[error(transparent)]
    Store(#[from] RedisStoreError),
}

#[derive(Clone, Copy)]
struct CandidateLocation {
    account_index: usize,
    deployment_index: usize,
}

struct CandidateObservation {
    latency_ewma_ms: Option<f64>,
    active_exclusion: Option<String>,
}

struct SchedulingCandidate {
    location: CandidateLocation,
    routing: AccountPoolCandidate,
}

fn candidate_locations(snapshot: &RuntimeConfigSnapshot, model: &str) -> Vec<CandidateLocation> {
    snapshot
        .accounts
        .iter()
        .enumerate()
        .flat_map(|(account_index, account)| {
            account.deployments.iter().enumerate().filter_map(
                move |(deployment_index, deployment)| {
                    (deployment.public_model == model).then_some(CandidateLocation {
                        account_index,
                        deployment_index,
                    })
                },
            )
        })
        .collect()
}

fn build_candidates(
    snapshot: &RuntimeConfigSnapshot,
    locations: &[CandidateLocation],
    account_states: &HashMap<usize, AccountSnapshot>,
    observations: Vec<CandidateObservation>,
    now: f64,
) -> Result<Vec<SchedulingCandidate>, AccountPoolSchedulerError> {
    locations
        .iter()
        .copied()
        .zip(observations)
        .map(|(location, observation)| {
            let account = &snapshot.accounts[location.account_index];
            let deployment = &account.deployments[location.deployment_index];
            let account_state = account_states
                .get(&location.account_index)
                .ok_or(AccountPoolSchedulerError::CandidateMissing)?;
            Ok(SchedulingCandidate {
                location,
                routing: routing_candidate(
                    account,
                    deployment,
                    account_state,
                    observation.active_exclusion.as_deref(),
                    observation.latency_ewma_ms,
                    now,
                ),
            })
        })
        .collect()
}

fn routing_candidate(
    account: &RuntimeAccount,
    deployment: &RuntimeDeployment,
    snapshot: &AccountSnapshot,
    active_exclusion: Option<&str>,
    latency_ewma_ms: Option<f64>,
    now: f64,
) -> AccountPoolCandidate {
    let cost_evidence = deployment.cost_evidence.as_ref();
    AccountPoolCandidate {
        account_id: account.id.clone(),
        deployment_id: deployment.litellm_model_id.clone(),
        billing_route_id: deployment.billing_route_id.clone(),
        available: configuration_reason(deployment).is_none()
            && unavailable_reason(snapshot, active_exclusion, now).is_none(),
        priority: account.priority,
        weight: deployment.routing_weight.unwrap_or(account.weight),
        manual_order: deployment.manual_order,
        inflight: snapshot.inflight,
        max_concurrency: snapshot.max_concurrency,
        remaining_quota_ratio: quota_ratio(account, snapshot),
        latency_ewma_ms,
        effective_cost: cost_evidence.map(|evidence| evidence.effective_cost.as_f64()),
        cost_currency: cost_evidence.and_then(|evidence| evidence.currency.clone()),
        cost_unit: cost_evidence.and_then(|evidence| evidence.unit.clone()),
        cost_partial: cost_evidence.is_some_and(|evidence| evidence.partial),
        cost_included: cost_evidence
            .is_some_and(|evidence| evidence.kind == CostEvidenceKind::SubscriptionIncluded),
    }
}

fn policy_strategy(snapshot: &RuntimeConfigSnapshot, model: &str) -> Strategy {
    snapshot
        .policies
        .iter()
        .find(|policy| policy.model == model)
        .map(|policy| policy.strategy)
        .unwrap_or(Strategy::QuotaAwareLeastInflight)
}

impl From<Strategy> for AccountPoolStrategy {
    fn from(value: Strategy) -> Self {
        match value {
            Strategy::Priority => Self::Priority,
            Strategy::Random => Self::Random,
            Strategy::LowestLatency => Self::LowestLatency,
            Strategy::HighestRemainingQuota => Self::HighestRemainingQuota,
            Strategy::LowestEffectiveCost => Self::LowestEffectiveCost,
            Strategy::LeastInflight => Self::LeastInflight,
            Strategy::WeightedRoundRobin => Self::WeightedRoundRobin,
            Strategy::QuotaAwareLeastInflight => Self::QuotaAwareLeastInflight,
        }
    }
}

fn configuration_reason(deployment: &RuntimeDeployment) -> Option<&'static str> {
    if !deployment.enabled {
        return Some("deployment_disabled");
    }
    deployment.routing_paused.then_some("manual_pause")
}

fn unavailable_reason<'a>(
    snapshot: &AccountSnapshot,
    active_exclusion: Option<&'a str>,
    now: f64,
) -> Option<&'a str> {
    if let Some(reason) = active_exclusion {
        return Some(reason);
    }
    if !snapshot.enabled || snapshot.health == Health::Disabled {
        return Some("disabled");
    }
    if snapshot.health == Health::Unhealthy {
        return Some("unhealthy");
    }
    if snapshot.cooldown_until.is_some_and(|until| until > now) {
        return Some("cooldown");
    }
    if snapshot.inflight >= snapshot.max_concurrency {
        return Some("capacity");
    }
    if snapshot
        .quota
        .total
        .is_some_and(|remaining| remaining <= 0.0)
    {
        return Some("total_quota");
    }
    if snapshot
        .quota
        .five_hour
        .is_some_and(|remaining| remaining <= 0.0)
    {
        return Some("five_hour_quota");
    }
    snapshot
        .quota
        .weekly
        .is_some_and(|remaining| remaining <= 0.0)
        .then_some("weekly_quota")
}

fn quota_ratio(account: &RuntimeAccount, snapshot: &AccountSnapshot) -> Option<f64> {
    [
        (snapshot.quota.total, account.quotas.total),
        (snapshot.quota.five_hour, account.quotas.five_hour),
        (snapshot.quota.weekly, account.quotas.weekly),
    ]
    .into_iter()
    .filter_map(|(remaining, limit)| match (remaining, limit) {
        (Some(remaining), Some(limit)) if limit > 0.0 => Some(remaining / limit),
        _ => None,
    })
    .min_by(f64::total_cmp)
}

fn push_unique_reason(reasons: &mut Vec<String>, reason: impl AsRef<str>) {
    let reason = reason.as_ref();
    if !reasons.iter().any(|existing| existing == reason) {
        reasons.push(reason.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::{
        candidate_locations, policy_strategy, quota_ratio, routing_candidate, unavailable_reason,
    };
    use crate::account_pool::config::{
        QuotaUnit, RuntimeAccount, RuntimeBillingMode, RuntimeConfigSnapshot, RuntimeDeployment,
        RuntimeModelPolicy, RuntimeQuotaConfig, Strategy,
    };
    use crate::account_pool::types::{AccountSnapshot, Health, QuotaSnapshot};

    fn deployment(id: &str, enabled: bool, routing_paused: bool) -> RuntimeDeployment {
        RuntimeDeployment {
            public_model: "model-a".to_string(),
            litellm_model_id: id.to_string(),
            binding_id: None,
            billing_route_id: None,
            billing_mode: RuntimeBillingMode::ProviderDecided,
            cost_evidence: None,
            manual_order: None,
            routing_weight: None,
            routing_paused,
            enabled,
        }
    }

    fn account(deployments: Vec<RuntimeDeployment>) -> RuntimeAccount {
        RuntimeAccount {
            id: "account-a".to_string(),
            channel_id: None,
            enabled: true,
            max_concurrency: 4,
            priority: 200,
            weight: 2,
            quotas: RuntimeQuotaConfig {
                unit: QuotaUnit::Tokens,
                total: Some(100.0),
                five_hour: Some(50.0),
                weekly: None,
            },
            quota_windows: Vec::new(),
            deployments,
        }
    }

    fn state() -> AccountSnapshot {
        AccountSnapshot {
            account_id: "account-a".to_string(),
            enabled: true,
            health: Health::Healthy,
            inflight: 1,
            max_concurrency: 4,
            cooldown_until: None,
            consecutive_failures: 0,
            reason_code: None,
            quota: QuotaSnapshot {
                total: Some(80.0),
                five_hour: Some(20.0),
                weekly: None,
            },
        }
    }

    fn snapshot(policies: Vec<RuntimeModelPolicy>) -> RuntimeConfigSnapshot {
        RuntimeConfigSnapshot {
            schema_version: 1,
            revision: "a".repeat(64),
            generated_at: "2026-08-28T12:00:00Z".to_string(),
            lease_ttl_seconds: 60,
            maximum_lease_seconds: 600,
            accounts: vec![account(vec![deployment("enabled", true, false)])],
            policies,
        }
    }

    #[test]
    fn runtime_candidate_uses_live_state_and_configured_quota_limits() {
        let account = account(vec![deployment("enabled", true, false)]);
        let candidate = routing_candidate(
            &account,
            &account.deployments[0],
            &state(),
            None,
            Some(42.0),
            100.0,
        );

        assert!(candidate.available);
        assert_eq!(candidate.weight, 2);
        assert_eq!(candidate.remaining_quota_ratio, Some(0.4));
        assert_eq!(candidate.latency_ewma_ms, Some(42.0));
    }

    #[test]
    fn configured_pauses_and_live_capacity_make_candidates_unavailable() {
        let paused = account(vec![deployment("paused", true, true)]);
        let paused_candidate =
            routing_candidate(&paused, &paused.deployments[0], &state(), None, None, 100.0);
        let saturated = AccountSnapshot {
            inflight: 4,
            ..state()
        };

        assert!(!paused_candidate.available);
        assert_eq!(
            unavailable_reason(&saturated, None, 100.0),
            Some("capacity")
        );
        assert_eq!(
            unavailable_reason(&state(), Some("rate_limited"), 100.0),
            Some("rate_limited")
        );
    }

    #[test]
    fn policies_default_to_quota_aware_least_inflight() {
        let default_snapshot = snapshot(Vec::new());
        let configured_snapshot = snapshot(vec![RuntimeModelPolicy {
            model: "model-a".to_string(),
            strategy: Strategy::LowestLatency,
            version: 1,
        }]);

        assert_eq!(
            policy_strategy(&default_snapshot, "model-a"),
            Strategy::QuotaAwareLeastInflight
        );
        assert_eq!(
            policy_strategy(&configured_snapshot, "model-a"),
            Strategy::LowestLatency
        );
        assert_eq!(
            candidate_locations(&configured_snapshot, "model-a").len(),
            1
        );
        assert_eq!(
            quota_ratio(&configured_snapshot.accounts[0], &state()),
            Some(0.4)
        );
    }
}
