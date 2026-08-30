//! 本文件把请求结果转换为 Redis Lua 使用的健康状态和资格限制变更。

use super::types::{Lease, SettleRequest};

const DEFAULT_RATE_LIMIT_SECONDS: f64 = 15.0;
const TRANSIENT_FAILURE_COOLDOWN_SECONDS: f64 = 30.0;

pub(crate) struct SettlementTransition {
    pub action: &'static str,
    pub reason_code: &'static str,
    pub cooldown_until: f64,
    pub exclusion_scope: &'static str,
    pub exclusion_source: &'static str,
    pub retry_at: f64,
}

pub(crate) fn classify_settlement(
    request: &SettleRequest,
    lease: &Lease,
    now: f64,
) -> SettlementTransition {
    let (action, reason_code, cooldown_until) = transition_fields(request, now);
    let (exclusion_scope, exclusion_source) = exclusion_fields(action, reason_code, lease);
    SettlementTransition {
        action,
        reason_code,
        cooldown_until,
        exclusion_scope,
        exclusion_source,
        retry_at: cooldown_until,
    }
}

fn transition_fields(request: &SettleRequest, now: f64) -> (&'static str, &'static str, f64) {
    if request.success {
        return ("success", "", 0.0);
    }
    let provider_code = request
        .provider_error_code
        .as_deref()
        .map(str::trim)
        .map(str::to_ascii_lowercase);
    if request.error_type.as_deref() == Some("provider_auth") || request.status_code == Some(401) {
        return ("disable", "credential_invalid", 0.0);
    }
    if request.status_code == Some(403) {
        return ("observe", "permission_denied", 0.0);
    }
    if request.status_code == Some(404) {
        return ("observe", "model_not_found", 0.0);
    }
    if request.status_code == Some(429) {
        let cooldown = request
            .retry_after_seconds
            .unwrap_or(DEFAULT_RATE_LIMIT_SECONDS);
        return (
            "cooldown",
            rate_limit_reason(provider_code.as_deref(), request.error_type.as_deref()),
            now + cooldown,
        );
    }
    if request
        .status_code
        .is_some_and(|status| (400..500).contains(&status))
    {
        return ("observe", "request_rejected", 0.0);
    }
    if request.status_code.is_some_and(|status| status >= 500) {
        return (
            "transient_failure",
            "upstream_unavailable",
            now + TRANSIENT_FAILURE_COOLDOWN_SECONDS,
        );
    }
    (
        "transient_failure",
        "transport_failure",
        now + TRANSIENT_FAILURE_COOLDOWN_SECONDS,
    )
}

fn rate_limit_reason(provider_code: Option<&str>, error_type: Option<&str>) -> &'static str {
    if matches!(
        provider_code,
        Some("concurrency_limit" | "concurrency_limit_exceeded" | "too_many_concurrent_requests")
    ) {
        return "concurrency_limited";
    }
    if matches!(
        provider_code,
        Some(
            "billing_hard_limit_reached"
                | "credit_balance_too_low"
                | "insufficient_balance"
                | "insufficient_credits"
        )
    ) {
        return "balance_signal_unscoped";
    }
    if matches!(provider_code, Some("insufficient_quota" | "quota_exceeded")) {
        return "quota_signal_unscoped";
    }
    if provider_code.is_none() && error_type != Some("provider_rate_limit") {
        return "rate_limit_unknown";
    }
    "rate_limited"
}

fn exclusion_fields(
    action: &str,
    reason_code: &str,
    lease: &Lease,
) -> (&'static str, &'static str) {
    if reason_code.is_empty() || reason_code == "request_rejected" || action == "success" {
        return ("", "");
    }
    if matches!(action, "disable" | "transient_failure") {
        return ("channel", "health");
    }
    if action == "observe" {
        return ("deployment", "health");
    }
    let scope = if lease.billing_route_id.is_some()
        && matches!(
            reason_code,
            "balance_signal_unscoped" | "quota_signal_unscoped"
        ) {
        "billing_route"
    } else {
        "deployment"
    };
    let source = if reason_code == "concurrency_limited" {
        "capacity"
    } else {
        "restriction"
    };
    (scope, source)
}

#[cfg(test)]
mod tests {
    use super::classify_settlement;
    use crate::account_pool::types::{Lease, SettleRequest};

    fn lease() -> Lease {
        Lease {
            lease_id: "lease".to_string(),
            generation_id: None,
            request_id: "request".to_string(),
            account_id: "account".to_string(),
            deployment_id: "deployment".to_string(),
            public_model: "model".to_string(),
            billing_route_id: Some("billing".to_string()),
            probe: false,
            expires_at: 10.0,
            absolute_expires_at: 20.0,
            settled: false,
            released: false,
        }
    }

    fn settlement(status_code: Option<u16>) -> SettleRequest {
        SettleRequest {
            lease_id: "lease".to_string(),
            success: false,
            status_code,
            input_tokens: 0,
            output_tokens: 0,
            cost_usd: None,
            latency_ms: None,
            error_type: None,
            provider_error_code: None,
            retry_after_seconds: None,
        }
    }

    #[test]
    fn classifies_auth_rate_limit_and_transport_failures() {
        let auth = classify_settlement(&settlement(Some(401)), &lease(), 100.0);
        assert_eq!(
            (auth.action, auth.reason_code),
            ("disable", "credential_invalid")
        );

        let mut rate_limit = settlement(Some(429));
        rate_limit.provider_error_code = Some("insufficient_quota".to_string());
        let rate_limit = classify_settlement(&rate_limit, &lease(), 100.0);
        assert_eq!(rate_limit.exclusion_scope, "billing_route");
        assert_eq!(rate_limit.cooldown_until, 115.0);

        let transport = classify_settlement(&settlement(None), &lease(), 100.0);
        assert_eq!(transport.reason_code, "transport_failure");
        assert_eq!(transport.cooldown_until, 130.0);
    }
}
