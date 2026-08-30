//! 本文件定义纯 Rust 模型路由器，负责从 `model_list` 中选择 Deployment。
//!
//! 路由器只执行无 I/O 的选择逻辑，网关宿主负责使用选中的 Deployment 发起供应商请求；
//! `deployment` 保存配置数据类型，`strategy` 保存通用选择策略。

mod deployment;
mod strategy;

pub mod account_pool;

pub use deployment::{Deployment, LiteLLMParams};
pub use strategy::RoutingStrategy;

/// Load-balancing router over a `model_list`.
#[derive(Clone, Debug, Default)]
pub struct Router {
    model_list: Vec<Deployment>,
    routing_strategy: RoutingStrategy,
}

impl Router {
    /// Build a router from a `model_list` using the default `simple-shuffle` strategy.
    pub fn new(model_list: Vec<Deployment>) -> Self {
        Self {
            model_list,
            routing_strategy: RoutingStrategy::SimpleShuffle,
        }
    }

    /// All deployments in the `model_list`. Read-only; used by the host to
    /// enumerate upstreams (e.g. to pre-warm a connection pool per deployment).
    pub fn deployments(&self) -> &[Deployment] {
        &self.model_list
    }

    /// Whether any deployment is registered under `model`.
    pub fn has_deployment(&self, model: &str) -> bool {
        self.model_list
            .iter()
            .any(|deployment| deployment.model_name == model)
    }

    /// Pick a deployment for `model` per the routing strategy. Returns `None`
    /// when no deployment is registered under that `model_name`.
    pub fn get_available_deployment(&self, model: &str) -> Option<&Deployment> {
        let candidates: Vec<&Deployment> = self
            .model_list
            .iter()
            .filter(|deployment| deployment.model_name == model)
            .collect();
        self.routing_strategy.select(&candidates)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn deployment(name: &str, model: &str) -> Deployment {
        Deployment {
            model_name: name.to_string(),
            litellm_params: LiteLLMParams {
                model: model.to_string(),
                api_key: None,
                api_base: None,
            },
        }
    }

    #[test]
    fn selects_a_matching_deployment() {
        let router = Router::new(vec![
            deployment("gpt-realtime", "gpt-realtime"),
            deployment("other", "other-model"),
        ]);
        let chosen = router
            .get_available_deployment("gpt-realtime")
            .expect("a deployment should match");
        assert_eq!(chosen.model_name, "gpt-realtime");
    }

    #[test]
    fn unknown_model_returns_none() {
        let router = Router::new(vec![deployment("gpt-realtime", "gpt-realtime")]);
        assert!(router.get_available_deployment("missing").is_none());
    }
}
