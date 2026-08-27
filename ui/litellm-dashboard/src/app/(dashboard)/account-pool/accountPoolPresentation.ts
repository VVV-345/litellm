import type {
  AdministrativeState,
  ChannelPriority,
  HealthRuntimeSnapshot,
  JsonDecimal,
  ParserOverviewState,
} from "./types";

export const accountPoolTableColumnDividerClass =
  "[&_th:not(:last-child)]:border-r [&_th:not(:last-child)]:border-border/70 [&_td:not(:last-child)]:border-r [&_td:not(:last-child)]:border-border/70";

export const administrativeStatePresentation: Record<AdministrativeState, { label: string; className: string }> = {
  enabled: { label: "启用", className: "border-emerald-300 bg-emerald-50 text-emerald-800" },
  paused: { label: "暂停", className: "border-amber-300 bg-amber-50 text-amber-900" },
  disabled: { label: "停用", className: "border-slate-300 bg-slate-100 text-slate-700" },
  pending_delete: { label: "待删除", className: "border-red-300 bg-red-50 text-red-800" },
};

export const channelPriorityPresentation: Record<ChannelPriority, { label: string; className: string }> = {
  400: { label: "最高", className: "border-violet-300 bg-violet-50 text-violet-800" },
  300: { label: "高", className: "border-sky-300 bg-sky-50 text-sky-800" },
  200: { label: "中", className: "border-slate-300 bg-slate-50 text-slate-700" },
  100: { label: "低", className: "border-orange-300 bg-orange-50 text-orange-900" },
};

export const healthPresentation: Record<HealthRuntimeSnapshot["health"], { label: string; className: string }> = {
  unknown: { label: "等待请求", className: "border-slate-300 bg-slate-50 text-slate-700" },
  healthy: { label: "正常", className: "border-emerald-300 bg-emerald-50 text-emerald-800" },
  degraded: { label: "异常", className: "border-amber-300 bg-amber-50 text-amber-900" },
  unhealthy: { label: "不可用", className: "border-red-300 bg-red-50 text-red-800" },
  half_open: { label: "半开探测", className: "border-sky-300 bg-sky-50 text-sky-800" },
  cooldown: { label: "冷却中", className: "border-orange-300 bg-orange-50 text-orange-900" },
  disabled: { label: "已停用", className: "border-slate-300 bg-slate-100 text-slate-700" },
};

export const parserOverviewLabels: Record<ParserOverviewState, string> = {
  loaded: "已解析",
  not_run: "未解析",
  unavailable: "解析数据不可用",
  invalid: "解析数据异常",
};

export const formatAccountPoolNumber = (
  value: JsonDecimal | null,
  maximumFractionDigits = 2,
  fallback = "未知",
): string => {
  if (value === null) return fallback;
  const numericValue = Number(value);
  return Number.isFinite(numericValue)
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(numericValue)
    : fallback;
};

export const formatAccountPoolDateTime = (value: string | null, fallback = "暂无"): string => {
  if (value === null) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString("zh-CN", { hour12: false });
};

export const formatAccountPoolEpoch = (value: number | null, fallback = "-"): string => {
  if (value === null) return fallback;
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? fallback : date.toLocaleString("zh-CN", { hour12: false });
};

export const parseOptionalNumber = (value: string): number | null => (value.trim() ? Number(value) : null);
