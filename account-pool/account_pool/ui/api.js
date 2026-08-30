// 本文件封装 4100 控制台请求，并将 LiteLLM 管理令牌限制在当前浏览器会话。
const TOKEN_KEY = "account-pool-admin-token";

export const getToken = () => sessionStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (token) => sessionStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

const request = async (path, options = {}) => {
  const response = await fetch(`/ui-api${path}`, {
    ...options,
    headers: {
      accept: "application/json",
      authorization: `Bearer ${getToken()}`,
      ...(options.body ? { "content-type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("account-pool:unauthorized"));
    throw new Error("LiteLLM 管理令牌无效");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : payload.detail?.error ?? payload.detail?.code;
    const error = new Error(detail || payload.message || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
};

export const api = {
  channels: () => request("/channels"),
  overview: () => request("/overview"),
  channel: (id) => request(`/channels/${encodeURIComponent(id)}`),
  models: () => request("/models"),
  stats: () => request("/stats"),
  litellmStatus: () => request("/litellm/status"),
  upstreamProviders: () => request("/upstream-providers"),
  routingTable: (model) => request(`/models/${encodeURIComponent(model)}/routing-table`),
  routingPolicy: (model) => request(`/models/${encodeURIComponent(model)}/routing-policy`),
  discoverUpstreamModels: (body) => request("/upstream-providers/discover-models", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  createChannel: (body) => request("/channels", {
    method: "POST",
    headers: { "idempotency-key": crypto.randomUUID() },
    body: JSON.stringify(body),
  }),
  updateChannel: (id, body) => request(`/channels/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "idempotency-key": crypto.randomUUID() },
    body: JSON.stringify(body),
  }),
  deleteChannel: (id) => request(`/channels/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { "idempotency-key": crypto.randomUUID() },
    body: JSON.stringify({ delete_mode: "delete_managed_deployment" }),
  }),
  updateRoutingPolicy: (model, body) => request(`/models/${encodeURIComponent(model)}/routing-policy`, {
    method: "PUT",
    body: JSON.stringify(body),
  }),
  updateRoutingCandidate: (model, bindingId, body) => request(
    `/models/${encodeURIComponent(model)}/routing-candidates/${encodeURIComponent(bindingId)}`,
    { method: "PUT", body: JSON.stringify(body) },
  ),
  deleteRoutingCandidate: (model, bindingId, body) => request(
    `/models/${encodeURIComponent(model)}/routing-candidates/${encodeURIComponent(bindingId)}`,
    { method: "DELETE", body: JSON.stringify(body) },
  ),
};
