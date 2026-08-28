//! HTTP boundary for account-pool request forwarding.

use axum::Router;
use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use bytes::Bytes;
use futures_util::stream;
use serde_json::json;

use crate::account_pool::proxy::{
    AccountPoolProxyBody, AccountPoolProxyError, AccountPoolProxyRequest,
};
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new().route("/v1/*path", post(handle))
}

async fn handle(
    State(state): State<AppState>,
    Path(path): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    forward(path, state, headers, body).await
}

pub(crate) async fn forward(
    path: String,
    state: AppState,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if path.trim_matches('/').is_empty() {
        return error_response(StatusCode::NOT_FOUND, "invalid account-pool path");
    }
    if !state.account_pool_proxy.enabled() {
        return error_response(
            StatusCode::NOT_FOUND,
            "the Rust account-pool gateway is disabled",
        );
    }
    let headers = headers
        .iter()
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.to_string(), value.to_string()))
        })
        .collect();
    match state
        .account_pool_proxy
        .forward(AccountPoolProxyRequest {
            path,
            headers,
            body: body.to_vec(),
        })
        .await
    {
        Ok(response) => upstream_response(response),
        Err(error) => proxy_error_response(error),
    }
}

fn upstream_response(response: crate::account_pool::proxy::AccountPoolProxyResponse) -> Response {
    let mut builder = Response::builder()
        .status(StatusCode::from_u16(response.status).unwrap_or(StatusCode::BAD_GATEWAY));
    for (name, value) in response.headers {
        let Ok(name) = HeaderName::try_from(name) else {
            continue;
        };
        let Ok(value) = HeaderValue::try_from(value) else {
            continue;
        };
        builder = builder.header(name, value);
    }
    let body = match response.body {
        AccountPoolProxyBody::Bytes(content) => Body::from(content),
        AccountPoolProxyBody::Stream(receiver) => {
            Body::from_stream(stream::unfold(receiver, |mut receiver| async move {
                receiver.recv().await.map(|chunk| (chunk, receiver))
            }))
        }
    };
    builder.body(body).unwrap_or_else(|_| {
        error_response(
            StatusCode::BAD_GATEWAY,
            "failed to construct the account-pool upstream response",
        )
    })
}

fn proxy_error_response(error: AccountPoolProxyError) -> Response {
    match error {
        AccountPoolProxyError::NotReady => error_response(
            StatusCode::SERVICE_UNAVAILABLE,
            "the Rust account-pool gateway is not ready",
        ),
        AccountPoolProxyError::InvalidJson => {
            error_response(StatusCode::BAD_REQUEST, "request body must be valid JSON")
        }
        AccountPoolProxyError::MissingModel => {
            error_response(StatusCode::BAD_REQUEST, "request body must contain a model")
        }
        AccountPoolProxyError::InvalidPath => {
            error_response(StatusCode::NOT_FOUND, "invalid account-pool path")
        }
        AccountPoolProxyError::InvalidApiKey => {
            error_response(StatusCode::UNAUTHORIZED, "invalid API key")
        }
        AccountPoolProxyError::UpstreamUnavailable => {
            error_response(StatusCode::BAD_GATEWAY, "LiteLLM Proxy is unavailable")
        }
        AccountPoolProxyError::NoRoute { model, reasons } => (
            StatusCode::SERVICE_UNAVAILABLE,
            axum::Json(json!({
                "error": {
                    "message": "No account capacity is available for this model",
                    "type": "account_pool_unavailable",
                    "details": {"model": model, "reasons": reasons},
                }
            })),
        )
            .into_response(),
    }
}

fn error_response(status: StatusCode, message: &'static str) -> Response {
    (
        status,
        axum::Json(json!({"error": {"message": message, "type": "gateway_error"}})),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use axum::body::Body;
    use axum::http::{HeaderMap, Request, StatusCode};
    use bytes::Bytes;
    use litellm_core::router::Router as ModelRouter;
    use tower::ServiceExt;

    use super::super::app;
    use crate::account_pool::{AccountPoolProxyRuntime, AccountPoolRuntime};
    use crate::io::realtime_pool::RealtimePool;
    use crate::state::AppState;

    fn state(proxy: AccountPoolProxyRuntime) -> AppState {
        AppState {
            router: Arc::new(ModelRouter::default()),
            master_key: None,
            loggers: Arc::new(Vec::new()),
            realtime_pool: RealtimePool::disabled(),
            account_pool: AccountPoolRuntime::disabled(),
            account_pool_proxy: proxy,
        }
    }

    #[tokio::test]
    async fn disabled_proxy_does_not_expose_a_generic_v1_forwarder() {
        let response = app(state(AccountPoolProxyRuntime::disabled()))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/chat/completions")
                    .body(Body::from(r#"{"model":"model-a"}"#))
                    .expect("request"),
            )
            .await
            .expect("response");

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn failed_proxy_rejects_requests_without_a_direct_route_fallback() {
        let response = app(state(AccountPoolProxyRuntime::failed()))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/messages")
                    .body(Body::from(r#"{"model":"model-a"}"#))
                    .expect("request"),
            )
            .await
            .expect("response");

        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    #[tokio::test]
    async fn empty_path_is_rejected_before_the_proxy_can_acquire_a_lease() {
        let response = super::forward(
            String::new(),
            state(AccountPoolProxyRuntime::failed()),
            HeaderMap::new(),
            Bytes::new(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
}
