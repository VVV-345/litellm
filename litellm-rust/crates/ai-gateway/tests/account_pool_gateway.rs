//! 本文件通过临时 Redis 验证账号池 HTTP 转发、流式响应、租约释放和并发限制。

#![cfg(feature = "server")]

use std::sync::Arc;
use std::time::Duration;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use futures_util::future::join_all;
use litellm_ai_gateway::account_pool::{
    AccountPoolProxyRuntime, AccountPoolProxySettings, AccountPoolRuntime, RuntimeConfigSettings,
};
use litellm_ai_gateway::io::realtime_pool::RealtimePool;
use litellm_ai_gateway::routes::app;
use litellm_ai_gateway::state::AppState;
use litellm_core::router::Router as ModelRouter;
use redis::AsyncCommands;
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::task::JoinSet;
use tokio::time::{Instant, sleep};
use tower::ServiceExt;
use uuid::Uuid;

const TEST_MODEL: &str = "account-pool-test-model";

#[tokio::test]
#[ignore = "requires ACCOUNT_POOL_TEST_REDIS_URL to point to a disposable Redis instance"]
async fn forwards_normal_and_streaming_requests_and_releases_the_lease() {
    let redis_url = std::env::var("ACCOUNT_POOL_TEST_REDIS_URL")
        .expect("ACCOUNT_POOL_TEST_REDIS_URL is required for this integration test");
    let account_id = format!("rust-account-pool-test-{}", Uuid::new_v4().simple());
    let deployment_id = format!("litellm-{account_id}");
    prime_account_state(&redis_url, &account_id, 1).await;

    let (upstream_url, mut received_requests, upstream_task) =
        upstream_server(2, Duration::ZERO).await;
    let (config_url, config_task) =
        config_server(runtime_snapshot(&account_id, &deployment_id, 1)).await;
    let runtime_settings = RuntimeConfigSettings::new(
        config_url,
        "test-control-plane-token".to_string(),
        Duration::from_secs(60),
    )
    .expect("runtime settings");
    let runtime = AccountPoolRuntime::start(runtime_settings)
        .await
        .expect("runtime starts");
    let proxy_settings = AccountPoolProxySettings::new(
        redis_url.clone(),
        upstream_url,
        false,
        Duration::from_secs(1),
    )
    .expect("proxy settings");
    let proxy = AccountPoolProxyRuntime::start(runtime.clone(), proxy_settings)
        .await
        .expect("proxy starts");
    let application = app(AppState {
        router: Arc::new(ModelRouter::default()),
        master_key: None,
        loggers: Arc::new(Vec::new()),
        realtime_pool: RealtimePool::disabled(),
        account_pool: runtime,
        account_pool_proxy: proxy,
    });

    let normal = application
        .clone()
        .oneshot(request(
            r#"{"model":"account-pool-test-model"}"#,
            "normal-request",
        ))
        .await
        .expect("normal response");
    assert_eq!(normal.status(), StatusCode::OK);
    let normal_body = to_bytes(normal.into_body(), usize::MAX)
        .await
        .expect("normal body");
    assert_eq!(
        normal_body.as_ref(),
        br#"{"id":"normal-response","usage":{"prompt_tokens":3,"completion_tokens":5}}"#
    );

    let streaming = application
        .oneshot(request(
            r#"{"model":"account-pool-test-model","stream":true}"#,
            "stream-request",
        ))
        .await
        .expect("streaming response");
    assert_eq!(streaming.status(), StatusCode::OK);
    let streaming_body = to_bytes(streaming.into_body(), usize::MAX)
        .await
        .expect("streaming body");
    assert_eq!(
        streaming_body.as_ref(),
        br#"data: {"id":"stream-response"}

data: {"usage":{"prompt_tokens":2,"completion_tokens":4}}

data: [DONE]

"#
    );

    let normal_request = received_requests
        .recv()
        .await
        .expect("normal upstream request");
    let streaming_request = received_requests
        .recv()
        .await
        .expect("streaming upstream request");
    assert_forwarded(&normal_request, &deployment_id, "normal-request", false);
    assert_forwarded(&streaming_request, &deployment_id, "stream-request", true);
    wait_for_inflight_zero(&redis_url, &account_id).await;

    upstream_task.await.expect("upstream task");
    config_task.abort();
}

#[tokio::test]
#[ignore = "requires ACCOUNT_POOL_TEST_REDIS_URL to point to a disposable Redis instance"]
async fn load_rejects_requests_over_the_redis_concurrency_limit() {
    const MAX_CONCURRENCY: u32 = 8;
    const REQUEST_COUNT: usize = 64;

    let redis_url = std::env::var("ACCOUNT_POOL_TEST_REDIS_URL")
        .expect("ACCOUNT_POOL_TEST_REDIS_URL is required for this integration test");
    let account_id = format!("rust-account-pool-load-{}", Uuid::new_v4().simple());
    let deployment_id = format!("litellm-{account_id}");
    prime_account_state(&redis_url, &account_id, MAX_CONCURRENCY).await;

    let (upstream_url, mut received_requests, upstream_task) = upstream_server(
        usize::try_from(MAX_CONCURRENCY).expect("concurrency fits usize"),
        Duration::from_millis(800),
    )
    .await;
    let (config_url, config_task) = config_server(runtime_snapshot(
        &account_id,
        &deployment_id,
        MAX_CONCURRENCY,
    ))
    .await;
    let runtime_settings = RuntimeConfigSettings::new(
        config_url,
        "test-control-plane-token".to_string(),
        Duration::from_secs(60),
    )
    .expect("runtime settings");
    let runtime = AccountPoolRuntime::start(runtime_settings)
        .await
        .expect("runtime starts");
    let proxy_settings = AccountPoolProxySettings::new(
        redis_url.clone(),
        upstream_url,
        false,
        Duration::from_secs(1),
    )
    .expect("proxy settings");
    let proxy = AccountPoolProxyRuntime::start(runtime.clone(), proxy_settings)
        .await
        .expect("proxy starts");
    let application = app(AppState {
        router: Arc::new(ModelRouter::default()),
        master_key: None,
        loggers: Arc::new(Vec::new()),
        realtime_pool: RealtimePool::disabled(),
        account_pool: runtime,
        account_pool_proxy: proxy,
    });

    let responses = join_all((0..REQUEST_COUNT).map(|index| {
        let application = application.clone();
        async move {
            application
                .oneshot(request(
                    r#"{"model":"account-pool-test-model"}"#,
                    format!("load-request-{index}"),
                ))
                .await
                .expect("load response")
                .status()
        }
    }))
    .await;
    let successful = responses
        .iter()
        .filter(|status| **status == StatusCode::OK)
        .count();
    let unavailable = responses
        .iter()
        .filter(|status| **status == StatusCode::SERVICE_UNAVAILABLE)
        .count();

    assert_eq!(
        successful,
        usize::try_from(MAX_CONCURRENCY).expect("concurrency fits usize")
    );
    assert_eq!(unavailable, REQUEST_COUNT - successful);
    for _ in 0..successful {
        let request = received_requests.recv().await.expect("forwarded request");
        assert!(request.starts_with("POST /v1/chat/completions HTTP/1.1"));
    }
    wait_for_inflight_zero(&redis_url, &account_id).await;

    upstream_task.await.expect("upstream task");
    config_task.abort();
}

fn request(body: impl Into<Body>, request_id: impl AsRef<str>) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri("/v1/chat/completions")
        .header("content-type", "application/json")
        .header("x-request-id", request_id.as_ref())
        .body(body.into())
        .expect("request")
}

fn runtime_snapshot(account_id: &str, deployment_id: &str, max_concurrency: u32) -> String {
    json!({
        "schema_version": 1,
        "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "generated_at": "2026-08-28T12:00:00Z",
        "lease_ttl_seconds": 60,
        "maximum_lease_seconds": 600,
        "accounts": [{
            "id": account_id,
            "channel_id": null,
            "enabled": true,
            "max_concurrency": max_concurrency,
            "priority": 100,
            "weight": 1,
            "quotas": {"unit": "tokens", "total": null, "five_hour": null, "weekly": null},
            "quota_windows": [],
            "deployments": [{
                "public_model": TEST_MODEL,
                "litellm_model_id": deployment_id,
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
        "policies": [{"model": TEST_MODEL, "strategy": "priority", "version": 1}]
    })
    .to_string()
}

async fn config_server(snapshot: String) -> (String, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("config bind");
    let address = listener.local_addr().expect("config address");
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("config request");
        let request = read_request(&mut socket).await;
        assert!(request.starts_with("GET /internal/runtime-config HTTP/1.1"));
        assert!(request.contains("x-account-pool-token: test-control-plane-token"));
        write_response(&mut socket, "application/json", &snapshot).await;
    });
    (format!("http://{address}/internal/runtime-config"), server)
}

async fn upstream_server(
    expected_requests: usize,
    response_delay: Duration,
) -> (String, mpsc::Receiver<String>, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("upstream bind");
    let address = listener.local_addr().expect("upstream address");
    let (sender, receiver) = mpsc::channel(expected_requests);
    let server = tokio::spawn(async move {
        let mut tasks = JoinSet::new();
        for _ in 0..expected_requests {
            let (mut socket, _) = listener.accept().await.expect("upstream request");
            let sender = sender.clone();
            tasks.spawn(async move {
                let request = read_request(&mut socket).await;
                let streaming = request.contains("\"stream\":true");
                sender.send(request).await.expect("records request");
                sleep(response_delay).await;
                if streaming {
                    write_response(
                        &mut socket,
                        "text/event-stream",
                        "data: {\"id\":\"stream-response\"}\n\ndata: {\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":4}}\n\ndata: [DONE]\n\n",
                    )
                    .await;
                } else {
                    write_response(
                        &mut socket,
                        "application/json",
                        "{\"id\":\"normal-response\",\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":5}}",
                    )
                    .await;
                }
            });
        }
        while let Some(result) = tasks.join_next().await {
            result.expect("upstream handler");
        }
    });
    (format!("http://{address}"), receiver, server)
}

async fn read_request(socket: &mut TcpStream) -> String {
    let mut received = Vec::new();
    let mut buffer = [0_u8; 4096];
    let header_end = loop {
        let count = socket.read(&mut buffer).await.expect("reads request");
        assert!(count > 0, "request closed before headers");
        received.extend_from_slice(&buffer[..count]);
        if let Some(index) = received.windows(4).position(|window| window == b"\r\n\r\n") {
            break index + 4;
        }
    };
    let headers = std::str::from_utf8(&received[..header_end]).expect("request headers utf8");
    let content_length = headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or_default();
    while received.len().saturating_sub(header_end) < content_length {
        let count = socket.read(&mut buffer).await.expect("reads request body");
        assert!(count > 0, "request closed before body");
        received.extend_from_slice(&buffer[..count]);
    }
    String::from_utf8(received).expect("request utf8")
}

async fn write_response(socket: &mut TcpStream, content_type: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: {content_type}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    );
    socket
        .write_all(response.as_bytes())
        .await
        .expect("writes response");
}

async fn prime_account_state(redis_url: &str, account_id: &str, max_concurrency: u32) {
    let client = redis::Client::open(redis_url).expect("Redis URL");
    let mut connection = client
        .get_multiplexed_async_connection()
        .await
        .expect("Redis connection");
    let state_key = format!("pool:account:{account_id}:state");
    let inflight_key = format!("pool:account:{account_id}:inflight");
    let max_concurrency = max_concurrency.to_string();
    let _: () = connection
        .hset_multiple(
            state_key,
            &[
                ("enabled", "1"),
                ("health", "healthy"),
                ("max_concurrency", max_concurrency.as_str()),
                ("consecutive_failures", "0"),
                ("cooldown_until", "0"),
            ],
        )
        .await
        .expect("account state");
    let _: () = connection
        .set(inflight_key, 0_u8)
        .await
        .expect("inflight state");
}

async fn wait_for_inflight_zero(redis_url: &str, account_id: &str) {
    let client = redis::Client::open(redis_url).expect("Redis URL");
    let mut connection = client
        .get_multiplexed_async_connection()
        .await
        .expect("Redis connection");
    let inflight_key = format!("pool:account:{account_id}:inflight");
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let inflight: Option<u32> = connection.get(&inflight_key).await.expect("reads inflight");
        if inflight.unwrap_or_default() == 0 {
            return;
        }
        assert!(Instant::now() < deadline, "lease was not released");
        sleep(Duration::from_millis(10)).await;
    }
}

fn assert_forwarded(request: &str, deployment_id: &str, request_id: &str, streaming: bool) {
    assert!(request.starts_with("POST /v1/chat/completions HTTP/1.1"));
    let (_, body) = request.split_once("\r\n\r\n").expect("request body");
    let body: Value = serde_json::from_str(body).expect("forwarded JSON");
    assert_eq!(body["model"], deployment_id);
    assert_eq!(body["metadata"]["account_pool_request_id"], request_id);
    assert_eq!(body["metadata"]["account_pool_public_model"], TEST_MODEL);
    assert!(body["metadata"]["account_pool_lease_id"].is_string());
    if streaming {
        assert_eq!(body["stream_options"]["include_usage"], true);
    }
}
