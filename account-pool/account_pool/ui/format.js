// 本文件集中处理号池状态、额度和调度策略的界面文本。
export const strategyNames = {
  priority: "渠道优先级",
  random: "随机",
  lowest_latency: "延迟优先",
  highest_remaining_quota: "剩余额度优先",
  lowest_effective_cost: "成本最低优先",
  least_inflight: "并发最少优先",
  weighted_round_robin: "权重轮询",
  quota_aware_least_inflight: "额度感知优先",
};

export const strategyOptions = Object.entries(strategyNames);

export const healthNames = {
  unknown: "等待请求",
  healthy: "正常",
  degraded: "异常",
  cooldown: "冷却中",
  disabled: "已停用",
};

export const priorityName = (value) => {
  if (value >= 400) return "最高";
  if (value >= 300) return "高";
  if (value >= 200) return "中";
  return "低";
};

export const formatNumber = (value) => value == null ? "不限" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);

export const formatPercent = (value) => value == null ? "未知" : `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value * 100)}%`;

export const formatTime = (value) => value == null ? "-" : new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });

export const formatChannelOperationMessage = (operation, action) => {
  if (operation.failure?.message) return operation.failure.message;
  if (operation.requires_key) return `${action}需要重新提供 API Key`;
  return `${action}已提交，状态：${operation.operation_status}`;
};

export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

export const statusBadge = (health) => `<span class="badge health-${escapeHtml(health)}">${escapeHtml(healthNames[health] ?? health)}</span>`;
