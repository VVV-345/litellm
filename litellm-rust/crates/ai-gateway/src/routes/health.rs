//! 本文件提供存活和就绪探针，并把账号池运行时状态纳入就绪判断。

use axum::Router;
use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::get;

use crate::state::AppState;

/// This route's contribution to the app router.
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health/liveness", get(liveness))
        .route("/health/readiness", get(readiness))
}

/// The process is up.
async fn liveness() -> StatusCode {
    StatusCode::OK
}

/// The server is ready to accept traffic.
async fn readiness(State(state): State<AppState>) -> StatusCode {
    if state.account_pool.status().await.ready && state.account_pool_proxy.ready() {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use axum::body::Body;
    use axum::http::Request;
    use litellm_core::router::Router as ModelRouter;
    use tower::ServiceExt;

    use super::{StatusCode, router};
    use crate::account_pool::{AccountPoolProxyRuntime, AccountPoolRuntime};
    use crate::io::realtime_pool::RealtimePool;
    use crate::state::AppState;

    fn state(account_pool: AccountPoolRuntime) -> AppState {
        AppState {
            router: Arc::new(ModelRouter::default()),
            master_key: None,
            loggers: Arc::new(Vec::new()),
            realtime_pool: RealtimePool::disabled(),
            account_pool,
            account_pool_proxy: AccountPoolProxyRuntime::disabled(),
        }
    }

    #[tokio::test]
    async fn readiness_fails_only_when_enabled_runtime_has_no_snapshot() {
        let disabled = router()
            .with_state(state(AccountPoolRuntime::disabled()))
            .oneshot(
                Request::builder()
                    .uri("/health/readiness")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");
        let unready = router()
            .with_state(state(AccountPoolRuntime::failed("unavailable".to_string())))
            .oneshot(
                Request::builder()
                    .uri("/health/readiness")
                    .body(Body::empty())
                    .expect("request"),
            )
            .await
            .expect("response");

        assert_eq!(disabled.status(), StatusCode::OK);
        assert_eq!(unready.status(), StatusCode::SERVICE_UNAVAILABLE);
    }
}
