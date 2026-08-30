//! 本文件汇总 HTTP 路由。每个路由模块负责声明自己的路径和处理器，`app` 只负责合并。
//!
//! 简单路由保持单文件；包含独立业务逻辑的路由按处理入口、服务逻辑和传输适配拆分。

pub mod account_pool;
pub mod gil;
pub mod health;
pub mod messages;
pub mod realtime;
pub mod responses;

use axum::Router;

use crate::state::AppState;

/// Assemble the application router by merging every route module's `router()`.
pub fn app(state: AppState) -> Router {
    let common = Router::new().merge(health::router()).merge(gil::router());
    let app = if state.account_pool_proxy.enabled() {
        common.merge(account_pool::router())
    } else {
        common
            .merge(messages::router())
            .merge(realtime::router())
            .merge(responses::router())
    };
    app.with_state(state)
}
