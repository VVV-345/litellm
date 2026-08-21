// 本文件统一将 Account Pool 的调度与不可用原因码转换为中文说明。

const reasonLabels: Record<string, string> = {
  capacity: "并发已满",
  cooldown: "冷却中",
  credential_invalid: "凭证无效",
  deployment_disabled: "绑定已停用",
  disabled: "渠道已停用",
  five_hour_exhausted: "五小时额度已耗尽",
  five_hour_quota: "五小时额度已耗尽",
  half_open_probe_inflight: "正在进行恢复探测",
  manual_pause: "模型绑定已暂停",
  model_not_found: "上游模型不存在",
  monthly_exhausted: "月额度已耗尽",
  no_available_route: "没有可用路由",
  no_model_bindings: "没有启用的模型绑定",
  pending_delete: "渠道等待删除",
  quota_generation_mismatch: "额度运行代次不一致",
  quota_persistence_unavailable: "额度持久化不可用",
  quota_recovery_isolation: "额度恢复隔离中",
  quota_state_invalid: "额度运行状态异常",
  quota_window_exhausted: "额度窗口已耗尽",
  rate_limited: "上游限流",
  rate_limit_unknown: "上游限流，恢复时间未知",
  routing_not_projected: "模型路由尚未投影",
  runtime_not_projected: "渠道运行配置尚未投影",
  runtime_state_unavailable: "渠道运行状态不可用",
  total_quota: "总额度已耗尽",
  unhealthy: "健康检查未通过",
  upstream_unavailable: "上游暂不可用",
  weekly_exhausted: "周额度已耗尽",
  weekly_quota: "周额度已耗尽",
};

export const reasonLabel = (code: string): string => reasonLabels[code] ?? code;

export const reasonLabelWithCode = (code: string): string => {
  const label = reasonLabel(code);
  return label === code ? code : `${label} (${code})`;
};
