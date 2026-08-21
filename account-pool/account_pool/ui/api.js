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
  accounts: () => request("/accounts"),
  models: () => request("/models"),
  stats: () => request("/stats"),
  litellmStatus: () => request("/litellm/status"),
  providerServices: () => request("/provider-services"),
  routingTable: (model) => request(`/models/${encodeURIComponent(model)}/routing-table`),
  routingPolicy: (model) => request(`/models/${encodeURIComponent(model)}/routing-policy`),
  validateProvider: (body) => request("/provider-services/validate", { method: "POST", body: JSON.stringify(body) }),
  createAccount: (body) => request("/accounts", { method: "POST", body: JSON.stringify(body) }),
  updateAccount: (id, body) => request(`/accounts/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteAccount: (id) => request(`/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),
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
