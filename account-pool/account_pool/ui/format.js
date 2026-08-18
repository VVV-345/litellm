// 本文件集中处理号池状态、额度和调度策略的界面文本。
export const strategyNames = {
  priority: "手动优先级",
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

export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

export const statusBadge = (health) => `<span class="badge health-${escapeHtml(health)}">${escapeHtml(healthNames[health] ?? health)}</span>`;
