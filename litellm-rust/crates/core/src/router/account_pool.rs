//! 本文件提供无 I/O 的账号池候选排序，供不同网关宿主共享并保持结果一致。

use std::cmp::Ordering;
use std::collections::HashSet;

use sha2::{Digest, Sha256};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AccountPoolStrategy {
    Priority,
    Random,
    LowestLatency,
    HighestRemainingQuota,
    LowestEffectiveCost,
    LeastInflight,
    WeightedRoundRobin,
    QuotaAwareLeastInflight,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AccountPoolCandidate {
    pub account_id: String,
    pub deployment_id: String,
    pub billing_route_id: Option<String>,
    pub available: bool,
    pub priority: i32,
    pub weight: u16,
    pub manual_order: Option<u64>,
    pub inflight: u32,
    pub max_concurrency: u32,
    pub remaining_quota_ratio: Option<f64>,
    pub latency_ewma_ms: Option<f64>,
    pub effective_cost: Option<f64>,
    pub cost_currency: Option<String>,
    pub cost_unit: Option<String>,
    pub cost_partial: bool,
    pub cost_included: bool,
}

impl AccountPoolCandidate {
    pub fn stable_id(&self) -> String {
        format!(
            "{}\0{}\0{}",
            self.account_id,
            self.deployment_id,
            self.billing_route_id.as_deref().unwrap_or_default()
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AccountPoolOrderReason {
    ManualOrder,
    ChannelPriority,
    RequestRandom,
    LatencyEwma,
    LatencyUnknown,
    RemainingQuotaRatio,
    QuotaUnknown,
    SubscriptionIncluded,
    CostUnknown,
    CostBasisConflict,
    EffectiveCostPartial,
    EffectiveCost,
    InflightRatio,
    ConfiguredWeight,
}

#[derive(Clone, Debug, PartialEq)]
pub struct AccountPoolOrder<'a> {
    pub candidate: &'a AccountPoolCandidate,
    pub reason_codes: Vec<AccountPoolOrderReason>,
    pub dynamic: bool,
}

pub fn order_candidates<'a>(
    candidates: &'a [AccountPoolCandidate],
    strategy: AccountPoolStrategy,
    model: &str,
    request_id: Option<&str>,
    sequence: Option<u64>,
) -> Vec<AccountPoolOrder<'a>> {
    let cost_basis = shared_cost_basis(candidates);
    let mut ordered: Vec<&AccountPoolCandidate> = candidates.iter().collect();
    ordered.sort_by(|left, right| {
        compare_candidates(
            left,
            right,
            strategy,
            model,
            request_id,
            cost_basis.as_ref(),
        )
    });
    let ordered = if strategy == AccountPoolStrategy::WeightedRoundRobin {
        weighted_order(&ordered, sequence.filter(|value| *value > 0).unwrap_or(1))
    } else {
        ordered
    };
    ordered
        .into_iter()
        .map(|candidate| AccountPoolOrder {
            candidate,
            reason_codes: order_reasons(candidate, strategy, cost_basis.as_ref()),
            dynamic: matches!(
                strategy,
                AccountPoolStrategy::Random | AccountPoolStrategy::WeightedRoundRobin
            ),
        })
        .collect()
}

fn compare_candidates(
    left: &AccountPoolCandidate,
    right: &AccountPoolCandidate,
    strategy: AccountPoolStrategy,
    model: &str,
    request_id: Option<&str>,
    cost_basis: Option<&(String, String)>,
) -> Ordering {
    availability_cmp(left, right)
        .then_with(|| match strategy {
            AccountPoolStrategy::Priority => option_cmp(left.manual_order, right.manual_order)
                .then_with(|| {
                    left.manual_order
                        .unwrap_or_default()
                        .cmp(&right.manual_order.unwrap_or_default())
                }),
            AccountPoolStrategy::Random => {
                random_rank(model, request_id, left).cmp(&random_rank(model, request_id, right))
            }
            AccountPoolStrategy::LowestLatency => {
                option_f64_cmp(left.latency_ewma_ms, right.latency_ewma_ms, false)
            }
            AccountPoolStrategy::HighestRemainingQuota => option_f64_cmp(
                left.remaining_quota_ratio,
                right.remaining_quota_ratio,
                true,
            )
            .then_with(|| inflight_cmp(left, right)),
            AccountPoolStrategy::LowestEffectiveCost => option_f64_cmp(
                comparable_cost(left, cost_basis),
                comparable_cost(right, cost_basis),
                false,
            ),
            AccountPoolStrategy::LeastInflight => inflight_cmp(left, right),
            AccountPoolStrategy::QuotaAwareLeastInflight => {
                inflight_cmp(left, right).then_with(|| {
                    option_f64_cmp(
                        left.remaining_quota_ratio,
                        right.remaining_quota_ratio,
                        true,
                    )
                })
            }
            AccountPoolStrategy::WeightedRoundRobin => Ordering::Equal,
        })
        .then_with(|| right.priority.cmp(&left.priority))
        .then_with(|| stable_cmp(left, right))
}

fn availability_cmp(left: &AccountPoolCandidate, right: &AccountPoolCandidate) -> Ordering {
    (!left.available).cmp(&(!right.available))
}

fn stable_cmp(left: &AccountPoolCandidate, right: &AccountPoolCandidate) -> Ordering {
    left.account_id
        .cmp(&right.account_id)
        .then_with(|| left.deployment_id.cmp(&right.deployment_id))
        .then_with(|| left.billing_route_id.cmp(&right.billing_route_id))
}

fn option_cmp<T: Ord>(left: Option<T>, right: Option<T>) -> Ordering {
    match (left, right) {
        (Some(left), Some(right)) => left.cmp(&right),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn option_f64_cmp(left: Option<f64>, right: Option<f64>, descending: bool) -> Ordering {
    match (left, right) {
        (Some(left), Some(right)) => {
            let ordering = left.total_cmp(&right);
            if descending {
                ordering.reverse()
            } else {
                ordering
            }
        }
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn inflight_cmp(left: &AccountPoolCandidate, right: &AccountPoolCandidate) -> Ordering {
    let left_ratio = f64::from(left.inflight) / f64::from(left.max_concurrency);
    let right_ratio = f64::from(right.inflight) / f64::from(right.max_concurrency);
    left_ratio.total_cmp(&right_ratio)
}

fn random_rank(
    model: &str,
    request_id: Option<&str>,
    candidate: &AccountPoolCandidate,
) -> [u8; 32] {
    let seed = request_id.unwrap_or("preview");
    Sha256::digest(format!("{model}\0{seed}\0{}", candidate.stable_id()).as_bytes()).into()
}

fn shared_cost_basis(candidates: &[AccountPoolCandidate]) -> Option<(String, String)> {
    let bases: HashSet<(String, String)> = candidates
        .iter()
        .filter_map(|candidate| {
            candidate.effective_cost?;
            Some((
                candidate.cost_currency.as_ref()?.to_lowercase(),
                candidate.cost_unit.as_ref()?.to_lowercase(),
            ))
        })
        .collect();
    (bases.len() == 1)
        .then(|| bases.into_iter().next())
        .flatten()
}

fn comparable_cost(
    candidate: &AccountPoolCandidate,
    basis: Option<&(String, String)>,
) -> Option<f64> {
    if candidate.cost_included {
        return Some(0.0);
    }
    let basis = basis?;
    let candidate_basis = (
        candidate.cost_currency.as_ref()?.to_lowercase(),
        candidate.cost_unit.as_ref()?.to_lowercase(),
    );
    (candidate_basis == *basis)
        .then_some(candidate.effective_cost)
        .flatten()
}

fn weighted_order<'a>(
    candidates: &[&'a AccountPoolCandidate],
    sequence: u64,
) -> Vec<&'a AccountPoolCandidate> {
    let available: Vec<&AccountPoolCandidate> = candidates
        .iter()
        .copied()
        .filter(|candidate| candidate.available)
        .collect();
    let unavailable = candidates
        .iter()
        .copied()
        .filter(|candidate| !candidate.available);
    if available.is_empty() {
        return unavailable.collect();
    }
    let wheel: Vec<&AccountPoolCandidate> = available
        .iter()
        .flat_map(|candidate| std::iter::repeat_n(*candidate, usize::from(candidate.weight)))
        .collect();
    let pivot = usize::try_from((sequence - 1) % wheel.len() as u64).unwrap_or_default();
    let rotated = wheel[pivot..].iter().chain(&wheel[..pivot]);
    let mut seen = HashSet::new();
    let unique = rotated
        .copied()
        .filter(|candidate| seen.insert(candidate.stable_id()));
    unique.chain(unavailable).collect()
}

fn order_reasons(
    candidate: &AccountPoolCandidate,
    strategy: AccountPoolStrategy,
    cost_basis: Option<&(String, String)>,
) -> Vec<AccountPoolOrderReason> {
    match strategy {
        AccountPoolStrategy::Priority => vec![if candidate.manual_order.is_some() {
            AccountPoolOrderReason::ManualOrder
        } else {
            AccountPoolOrderReason::ChannelPriority
        }],
        AccountPoolStrategy::Random => vec![AccountPoolOrderReason::RequestRandom],
        AccountPoolStrategy::LowestLatency => vec![if candidate.latency_ewma_ms.is_some() {
            AccountPoolOrderReason::LatencyEwma
        } else {
            AccountPoolOrderReason::LatencyUnknown
        }],
        AccountPoolStrategy::HighestRemainingQuota => {
            vec![if candidate.remaining_quota_ratio.is_some() {
                AccountPoolOrderReason::RemainingQuotaRatio
            } else {
                AccountPoolOrderReason::QuotaUnknown
            }]
        }
        AccountPoolStrategy::LowestEffectiveCost => {
            vec![cost_reason(candidate, cost_basis)]
        }
        AccountPoolStrategy::LeastInflight => vec![AccountPoolOrderReason::InflightRatio],
        AccountPoolStrategy::WeightedRoundRobin => {
            vec![AccountPoolOrderReason::ConfiguredWeight]
        }
        AccountPoolStrategy::QuotaAwareLeastInflight => vec![
            AccountPoolOrderReason::InflightRatio,
            AccountPoolOrderReason::RemainingQuotaRatio,
        ],
    }
}

fn cost_reason(
    candidate: &AccountPoolCandidate,
    basis: Option<&(String, String)>,
) -> AccountPoolOrderReason {
    if candidate.cost_included {
        return AccountPoolOrderReason::SubscriptionIncluded;
    }
    if candidate.effective_cost.is_none() {
        return AccountPoolOrderReason::CostUnknown;
    }
    if comparable_cost(candidate, basis).is_none() {
        return AccountPoolOrderReason::CostBasisConflict;
    }
    if candidate.cost_partial {
        AccountPoolOrderReason::EffectiveCostPartial
    } else {
        AccountPoolOrderReason::EffectiveCost
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use serde::Deserialize;

    use super::{
        AccountPoolCandidate, AccountPoolOrder, AccountPoolOrderReason, AccountPoolStrategy,
        order_candidates,
    };

    fn candidate(account_id: &str) -> AccountPoolCandidate {
        AccountPoolCandidate {
            account_id: account_id.to_string(),
            deployment_id: format!("deployment-{account_id}"),
            billing_route_id: None,
            available: true,
            priority: 0,
            weight: 1,
            manual_order: None,
            inflight: 0,
            max_concurrency: 10,
            remaining_quota_ratio: None,
            latency_ewma_ms: None,
            effective_cost: None,
            cost_currency: None,
            cost_unit: None,
            cost_partial: false,
            cost_included: false,
        }
    }

    fn ids<'a>(ordered: &'a [AccountPoolOrder<'a>]) -> Vec<&'a str> {
        ordered
            .iter()
            .map(|item| item.candidate.account_id.as_str())
            .collect()
    }

    #[test]
    fn priority_prefers_manual_order_then_channel_priority() {
        let candidates = [
            AccountPoolCandidate {
                priority: 400,
                ..candidate("high")
            },
            AccountPoolCandidate {
                priority: 100,
                manual_order: Some(1),
                ..candidate("manual-second")
            },
            AccountPoolCandidate {
                priority: 100,
                manual_order: Some(0),
                ..candidate("manual-first")
            },
        ];

        let ordered = order_candidates(
            &candidates,
            AccountPoolStrategy::Priority,
            "model-a",
            None,
            None,
        );

        assert_eq!(ids(&ordered), ["manual-first", "manual-second", "high"]);
        assert_eq!(
            ordered[0].reason_codes,
            [AccountPoolOrderReason::ManualOrder]
        );
    }

    #[test]
    fn random_is_stable_per_request_and_changes_between_requests() {
        let candidates: Vec<AccountPoolCandidate> = (0..6)
            .map(|index| candidate(&format!("account-{index}")))
            .collect();

        let first = order_candidates(
            &candidates,
            AccountPoolStrategy::Random,
            "model-a",
            Some("request-a"),
            None,
        );
        let duplicate = order_candidates(
            &candidates,
            AccountPoolStrategy::Random,
            "model-a",
            Some("request-a"),
            None,
        );
        let second = order_candidates(
            &candidates,
            AccountPoolStrategy::Random,
            "model-a",
            Some("request-b"),
            None,
        );

        assert_eq!(ids(&first), ids(&duplicate));
        assert_ne!(ids(&first), ids(&second));
        assert!(first.iter().all(|item| item.dynamic));
    }

    #[test]
    fn latency_cost_and_quota_put_unknown_evidence_last() {
        let candidates = [
            candidate("unknown"),
            AccountPoolCandidate {
                latency_ewma_ms: Some(300.0),
                effective_cost: Some(4.0),
                cost_currency: Some("USD".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                remaining_quota_ratio: Some(0.1),
                ..candidate("slow-expensive")
            },
            AccountPoolCandidate {
                latency_ewma_ms: Some(50.0),
                effective_cost: Some(1.0),
                cost_currency: Some("USD".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                remaining_quota_ratio: Some(0.8),
                ..candidate("fast-cheap")
            },
        ];

        let latency = order_candidates(
            &candidates,
            AccountPoolStrategy::LowestLatency,
            "model-a",
            None,
            None,
        );
        let cost = order_candidates(
            &candidates,
            AccountPoolStrategy::LowestEffectiveCost,
            "model-a",
            None,
            None,
        );
        let quota = order_candidates(
            &candidates,
            AccountPoolStrategy::HighestRemainingQuota,
            "model-a",
            None,
            None,
        );

        assert_eq!(ids(&latency), ["fast-cheap", "slow-expensive", "unknown"]);
        assert_eq!(ids(&cost), ["fast-cheap", "slow-expensive", "unknown"]);
        assert_eq!(ids(&quota), ["fast-cheap", "slow-expensive", "unknown"]);
    }

    #[test]
    fn cost_does_not_compare_different_bases() {
        let candidates = [
            AccountPoolCandidate {
                effective_cost: Some(2.0),
                cost_currency: Some("USD".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                ..candidate("usd")
            },
            AccountPoolCandidate {
                effective_cost: Some(1.0),
                cost_currency: Some("CREDITS".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                ..candidate("credits")
            },
        ];

        let ordered = order_candidates(
            &candidates,
            AccountPoolStrategy::LowestEffectiveCost,
            "model-a",
            None,
            None,
        );

        assert!(
            ordered
                .iter()
                .all(|item| { item.reason_codes == [AccountPoolOrderReason::CostBasisConflict] })
        );
    }

    #[test]
    fn unavailable_candidates_stay_last_for_every_strategy() {
        let candidates = [
            AccountPoolCandidate {
                available: false,
                priority: 400,
                latency_ewma_ms: Some(1.0),
                effective_cost: Some(0.0),
                cost_currency: Some("USD".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                remaining_quota_ratio: Some(1.0),
                ..candidate("unavailable")
            },
            AccountPoolCandidate {
                priority: 100,
                latency_ewma_ms: Some(100.0),
                effective_cost: Some(10.0),
                cost_currency: Some("USD".to_string()),
                cost_unit: Some("million_tokens".to_string()),
                remaining_quota_ratio: Some(0.1),
                ..candidate("available")
            },
        ];
        let strategies = [
            AccountPoolStrategy::Priority,
            AccountPoolStrategy::Random,
            AccountPoolStrategy::LowestLatency,
            AccountPoolStrategy::HighestRemainingQuota,
            AccountPoolStrategy::LowestEffectiveCost,
            AccountPoolStrategy::LeastInflight,
            AccountPoolStrategy::WeightedRoundRobin,
            AccountPoolStrategy::QuotaAwareLeastInflight,
        ];

        for strategy in strategies {
            let ordered = order_candidates(&candidates, strategy, "model-a", Some("request"), None);
            assert_eq!(ids(&ordered).last(), Some(&"unavailable"));
        }
    }

    #[test]
    fn weighted_round_robin_rotates_without_dropping_fallbacks() {
        let candidates = [
            AccountPoolCandidate {
                weight: 3,
                ..candidate("heavy")
            },
            candidate("light"),
        ];

        let first = order_candidates(
            &candidates,
            AccountPoolStrategy::WeightedRoundRobin,
            "model-a",
            None,
            Some(1),
        );
        let fourth = order_candidates(
            &candidates,
            AccountPoolStrategy::WeightedRoundRobin,
            "model-a",
            None,
            Some(4),
        );

        assert_eq!(ids(&first), ["heavy", "light"]);
        assert_eq!(ids(&fourth), ["light", "heavy"]);
    }

    #[test]
    fn matches_the_shared_python_routing_conformance_fixture() {
        let fixture: OrderingFixture = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../account-pool/testdata/routing_conformance.json"
        )))
        .expect("valid routing conformance fixture");
        let candidates = fixture
            .candidates
            .iter()
            .map(OrderingFixtureCandidate::candidate)
            .collect::<Vec<_>>();

        for (strategy, expected) in fixture.expected {
            let ordered = order_candidates(
                &candidates,
                fixture_strategy(&strategy),
                &fixture.model,
                Some(&fixture.request_id),
                Some(fixture.sequence),
            );
            let actual = ordered
                .iter()
                .map(|item| item.candidate.account_id.as_str())
                .collect::<Vec<_>>();
            assert_eq!(actual, expected);
        }
    }

    #[derive(Deserialize)]
    struct OrderingFixture {
        model: String,
        request_id: String,
        sequence: u64,
        candidates: Vec<OrderingFixtureCandidate>,
        expected: HashMap<String, Vec<String>>,
    }

    #[derive(Deserialize)]
    struct OrderingFixtureCandidate {
        account_id: String,
        deployment_id: String,
        billing_route_id: Option<String>,
        available: bool,
        priority: i32,
        weight: u16,
        manual_order: Option<u64>,
        inflight: u32,
        max_concurrency: u32,
        remaining_quota_ratio: Option<f64>,
        latency_ewma_ms: Option<f64>,
        effective_cost: Option<String>,
        cost_currency: Option<String>,
        cost_unit: Option<String>,
        cost_partial: bool,
        cost_included: bool,
    }

    impl OrderingFixtureCandidate {
        fn candidate(&self) -> AccountPoolCandidate {
            AccountPoolCandidate {
                account_id: self.account_id.clone(),
                deployment_id: self.deployment_id.clone(),
                billing_route_id: self.billing_route_id.clone(),
                available: self.available,
                priority: self.priority,
                weight: self.weight,
                manual_order: self.manual_order,
                inflight: self.inflight,
                max_concurrency: self.max_concurrency,
                remaining_quota_ratio: self.remaining_quota_ratio,
                latency_ewma_ms: self.latency_ewma_ms,
                effective_cost: self
                    .effective_cost
                    .as_deref()
                    .map(str::parse)
                    .transpose()
                    .expect("fixture cost is a finite number"),
                cost_currency: self.cost_currency.clone(),
                cost_unit: self.cost_unit.clone(),
                cost_partial: self.cost_partial,
                cost_included: self.cost_included,
            }
        }
    }

    fn fixture_strategy(value: &str) -> AccountPoolStrategy {
        match value {
            "priority" => AccountPoolStrategy::Priority,
            "random" => AccountPoolStrategy::Random,
            "lowest_latency" => AccountPoolStrategy::LowestLatency,
            "highest_remaining_quota" => AccountPoolStrategy::HighestRemainingQuota,
            "lowest_effective_cost" => AccountPoolStrategy::LowestEffectiveCost,
            "least_inflight" => AccountPoolStrategy::LeastInflight,
            "weighted_round_robin" => AccountPoolStrategy::WeightedRoundRobin,
            "quota_aware_least_inflight" => AccountPoolStrategy::QuotaAwareLeastInflight,
            _ => panic!("unknown routing strategy in fixture: {value}"),
        }
    }
}
