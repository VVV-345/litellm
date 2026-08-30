//! 本文件是 LiteLLM AI Gateway 的库入口，按功能开关拆分调用逻辑、I/O 辅助和 HTTP 服务。
//!
//! Python 扩展只依赖不包含 HTTP 服务的基础模块；启用 `server` 后才编译 Axum
//! 路由和共享状态，启用 `python-config` 后才加载 Python 配置读取器。

pub mod account_pool;
pub mod audio_transcription;
mod client;
pub mod io;
pub mod ocr;

/// GIL-activity tracking. Pure (atomics only); shared by the `server` routes and
/// the `python-config` reader, so it is available without either feature.
pub mod gil;

#[cfg(feature = "server")]
pub mod auth;
#[cfg(feature = "server")]
pub mod routes;
#[cfg(feature = "server")]
pub mod state;

mod constants;
pub mod integrations;
#[cfg(feature = "server")]
mod realtime;

#[cfg(feature = "python-config")]
pub mod python;
