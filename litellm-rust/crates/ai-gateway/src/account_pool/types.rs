//! 本文件定义 Rust 调度所需的请求、租约、结算和运行时状态数据类型。

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Health {
    Unknown,
    Healthy,
    Degraded,
    Unhealthy,
    HalfOpen,
    Cooldown,
    Disabled,
}

impl Health {
    pub(crate) fn parse(value: Option<&str>) -> Option<Self> {
        match value {
            Some("healthy") => Some(Self::Healthy),
            Some("degraded") => Some(Self::Degraded),
            Some("unhealthy") => Some(Self::Unhealthy),
            Some("half_open") => Some(Self::HalfOpen),
            Some("cooldown") => Some(Self::Cooldown),
            Some("disabled") => Some(Self::Disabled),
            None | Some("unknown") => Some(Self::Unknown),
            Some(_) => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct QuotaSnapshot {
    pub total: Option<f64>,
    pub five_hour: Option<f64>,
    pub weekly: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AccountSnapshot {
    pub account_id: String,
    pub enabled: bool,
    pub health: Health,
    pub inflight: u32,
    pub max_concurrency: u32,
    pub cooldown_until: Option<f64>,
    pub consecutive_failures: u32,
    pub reason_code: Option<String>,
    pub quota: QuotaSnapshot,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Lease {
    pub lease_id: String,
    pub generation_id: Option<String>,
    pub request_id: String,
    pub account_id: String,
    pub deployment_id: String,
    pub public_model: String,
    pub billing_route_id: Option<String>,
    pub probe: bool,
    pub expires_at: f64,
    pub absolute_expires_at: f64,
    pub settled: bool,
    pub released: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcquireRequest {
    pub request_id: String,
    pub model: String,
    pub estimated_tokens: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AcquiredLease {
    pub lease: Lease,
    pub lease_ttl_seconds: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AcquireResult {
    Acquired(AcquiredLease),
    Unavailable {
        model: String,
        reason_codes: Vec<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SettleRequest {
    pub lease_id: String,
    pub success: bool,
    pub status_code: Option<u16>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost_usd: Option<String>,
    pub latency_ms: Option<f64>,
    pub error_type: Option<String>,
    pub provider_error_code: Option<String>,
    pub retry_after_seconds: Option<f64>,
}
