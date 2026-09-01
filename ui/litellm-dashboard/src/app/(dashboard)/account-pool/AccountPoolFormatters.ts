/** 本文件提供号池状态、额度和时间的纯格式化逻辑，供卡片与配置弹窗复用。 */

import type {
  AccountPoolEnvironment,
  AccountPoolProxyProfile,
  AccountPoolQuotaSnapshot,
  AccountPoolQuotaWindow,
  AccountPoolStatus,
  AccountPoolUpdateRequest,
} from "./AccountPoolTypes";

const STATUS_LABELS: Record<AccountPoolStatus, string> = {
  provisioning: "创建中",
  awaiting_authorization: "等待授权",
  validating: "校验中",
  ready: "可用",
  cooling_down: "冷却中",
  disabled: "已停用",
  error: "异常",
  deleting: "删除中",
};

export const statusLabel = (status: AccountPoolStatus): string => STATUS_LABELS[status];

export const statusVariant = (status: AccountPoolStatus): "default" | "secondary" | "destructive" | "outline" => {
  if (status === "ready") return "default";
  if (status === "error") return "destructive";
  if (status === "cooling_down" || status === "disabled") return "secondary";
  return "outline";
};

export const mostConstrainedWindow = (environment: AccountPoolEnvironment): AccountPoolQuotaWindow | null => {
  const windows = [...environment.quota.windows, ...environment.model_quotas.flatMap((item) => item.quota.windows)];
  if (windows.length === 0) return null;
  return windows.reduce((lowest, window) => (window.remaining_percent < lowest.remaining_percent ? window : lowest));
};

export const formatQuota = (window: AccountPoolQuotaWindow | null): string =>
  window === null ? "尚未观测" : `${window.remaining_percent.toFixed(window.remaining_percent % 1 ? 1 : 0)}%`;

export const formatDateTime = (value: string | null | undefined): string => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

export const quotaRows = (
  environment: AccountPoolEnvironment,
): Array<{ key: string; label: string; quota: AccountPoolQuotaSnapshot }> => [
  { key: "account", label: "账号", quota: environment.quota },
  ...environment.model_quotas.map((item) => ({ key: item.model, label: item.model, quota: item.quota })),
];

const TRANSITIONAL_STATUSES: ReadonlySet<AccountPoolStatus> = new Set([
  "provisioning",
  "awaiting_authorization",
  "validating",
  "deleting",
]);

const isConfigurationPending = (environment: AccountPoolEnvironment): boolean =>
  environment.configuration_pending === true;

export const canToggleEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !TRANSITIONAL_STATUSES.has(environment.status) &&
  !(environment.status === "error" && environment.available_models.length === 0);

export const canConfigureEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  !TRANSITIONAL_STATUSES.has(environment.status) &&
  !(environment.status === "error" && environment.available_models.length === 0);

export const canDeleteEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) && environment.status !== "deleting";

export const canAuthorizeEnvironment = (environment: AccountPoolEnvironment): boolean =>
  !isConfigurationPending(environment) &&
  (environment.status === "awaiting_authorization" || environment.status === "error");

const SAFE_PROXY_PROTOCOLS: ReadonlySet<string> = new Set(["http:", "https:", "socks5:", "socks5h:"]);

const normalizeProtocol = (value: string): string => {
  const normalized = value.trim().toLowerCase();
  return normalized.endsWith(":") ? normalized : `${normalized}:`;
};

const profileProtocol = (profile: AccountPoolProxyProfile): string | null => {
  const declaredProtocol = profile.protocol ?? profile.scheme;
  if (declaredProtocol) return normalizeProtocol(declaredProtocol);
  const rawUrl = profile.proxy_url ?? profile.url;
  if (!rawUrl) return null;
  try {
    return new URL(rawUrl).protocol.toLowerCase();
  } catch {
    return "invalid:";
  }
};

export const validateProxyProfileSelection = (
  proxyMode: AccountPoolUpdateRequest["proxy_mode"],
  proxyProfileId: string | null,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (proxyMode === "default_gateway") return null;
  if (!proxyProfileId) return "请选择代理 Profile";
  if (profiles.length === 0) return "暂无可用代理 Profile，请先配置 Profile";
  const selectedProfile = profiles.find((profile) => profile.id === proxyProfileId);
  if (!selectedProfile) return "所选代理 Profile 已删除，请重新选择";
  const protocol = profileProtocol(selectedProfile);
  if (protocol === "invalid:") return "代理 Profile URL 无效";
  if (protocol !== null && !SAFE_PROXY_PROTOCOLS.has(protocol)) {
    return "代理 Profile URL 协议不安全，仅支持 HTTP、HTTPS 或 SOCKS5";
  }
  return null;
};

export const validateAccountPoolUpdate = (
  form: AccountPoolUpdateRequest,
  environment: AccountPoolEnvironment,
  profiles: readonly AccountPoolProxyProfile[],
): string | null => {
  if (!form.name.trim()) return "请输入环境名称";
  if (!Number.isInteger(form.concurrency_limit) || form.concurrency_limit < 1 || form.concurrency_limit > 1000) {
    return "并发数必须是 1 到 1000 之间的整数";
  }
  const unsupportedModel = form.enabled_models.find((model) => !environment.available_models.includes(model));
  if (unsupportedModel) return `模型 ${unsupportedModel} 当前不可用`;
  return validateProxyProfileSelection(form.proxy_mode, form.proxy_profile_id, profiles);
};
