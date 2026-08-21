// 本文件装配 4100 调度控制台，管理渠道、模型策略和实时路由状态。
import { api, clearToken, getToken, setToken } from "./api.js?v=4";
import { escapeHtml, formatNumber, priorityName, statusBadge, strategyNames } from "./format.js?v=4";
import { createRoutingWorkbench } from "./routing.js?v=4";

const state = {
  accounts: [],
  models: [],
  stats: null,
  status: null,
  manifests: [],
  selectedModels: [],
  validation: null,
  editingAccount: null,
  activeView: "overview",
};

const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const appView = $("#app-view");
const notice = $("#notice");
const channelDialog = $("#channel-dialog");
const channelForm = $("#channel-form");
const deleteDialog = $("#delete-dialog");

const viewMeta = {
  overview: ["运行概览", "查看号池容量、模型和渠道健康状态"],
  channels: ["渠道管理", "维护上游账号和调度参数"],
  routing: ["模型调度", "配置每个对外模型的选号策略和路由顺序"],
};

const showNotice = (message, error = false) => {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = false;
  window.setTimeout(() => { notice.hidden = true; }, 4500);
};

const showLogin = (message = "") => {
  clearToken();
  appView.hidden = true;
  loginView.hidden = false;
  $("#login-error").textContent = message;
  $("#admin-token").focus();
};

const showApp = () => {
  loginView.hidden = true;
  appView.hidden = false;
};

const setView = (view) => {
  state.activeView = view;
  for (const item of document.querySelectorAll("[data-view]")) item.classList.toggle("active", item.dataset.view === view);
  for (const name of Object.keys(viewMeta)) $(`#${name}-view`).hidden = name !== view;
  $("#page-title").textContent = viewMeta[view][0];
  $("#page-description").textContent = viewMeta[view][1];
};

const loadDashboard = async () => {
  const [accounts, models, stats, status, manifests] = await Promise.all([
    api.accounts(), api.models(), api.stats(), api.litellmStatus(), api.providerServices(),
  ]);
  Object.assign(state, { accounts, models, stats, status, manifests });
  await routing.sync(models);
  render();
};

const refresh = async (silent = false) => {
  try {
    await loadDashboard();
    showApp();
    if (!silent) showNotice("号池状态已刷新");
  } catch (error) {
    if (error.message !== "LiteLLM 管理令牌无效") showNotice(error.message, true);
  }
};

const routing = createRoutingWorkbench({ showNotice, refreshDashboard: refresh });

const render = () => {
  renderStatus();
  renderStats();
  renderModels("#overview-models", false);
  renderChannels("#overview-channels", false);
  renderChannels("#channel-table", true);
  $("#last-updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
};

const renderStatus = () => {
  const element = $("#service-state");
  const online = Boolean(state.status?.manageable);
  element.textContent = state.status?.message ?? "状态未知";
  element.className = `status-dot ${online ? "online" : "offline"}`;
};

const renderStats = () => {
  const values = [
    ["对外模型", state.stats?.models ?? 0],
    ["渠道总数", state.stats?.accounts ?? 0],
    ["可用渠道", state.stats?.available_accounts ?? 0],
    ["当前请求", state.stats?.inflight ?? 0],
    ["总并发容量", state.stats?.max_concurrency ?? 0],
  ];
  $("#stat-grid").innerHTML = values.map(([label, value]) => `<div class="stat-item"><span>${label}</span><strong>${formatNumber(value)}</strong></div>`).join("");
};

const renderModels = (selector, interactive) => {
  const rows = state.models.map((model) => `
    <tr>
      <td><strong>${escapeHtml(model.model)}</strong></td>
      <td>${escapeHtml(strategyNames[model.strategy] ?? model.strategy)}</td>
      <td>${model.available_accounts} / ${model.accounts}</td>
      <td>${model.inflight} / ${model.max_concurrency}</td>
      ${interactive ? `<td><button class="button ghost" data-select-model="${escapeHtml(model.model)}">查看路由</button></td>` : ""}
    </tr>`).join("");
  $(selector).innerHTML = `<table><thead><tr><th>模型</th><th>调度策略</th><th>可用渠道</th><th>并发</th>${interactive ? "<th>操作</th>" : ""}</tr></thead><tbody>${rows || `<tr><td class="empty" colspan="${interactive ? 5 : 4}">尚未配置模型</td></tr>`}</tbody></table>`;
};

const modelTags = (models) => `<div class="tag-row">${models.map((model) => `<span class="tag">${escapeHtml(model)}</span>`).join("")}</div>`;

const renderChannels = (selector, interactive) => {
  const rows = state.accounts.map((account) => `
    <tr>
      <td><strong>${escapeHtml(account.display_name)}</strong><div class="muted">${escapeHtml(account.group || account.id)}</div></td>
      <td>${escapeHtml(account.provider)}</td>
      <td>${modelTags(account.models)}</td>
      <td>${statusBadge(account.runtime.health)}</td>
      <td>${account.runtime.inflight} / ${account.runtime.max_concurrency}</td>
      <td>${priorityName(account.priority)} <span class="muted">(${account.priority})</span></td>
      <td>${account.weight}</td>
      <td>${formatNumber(account.runtime.quota?.total)}</td>
      ${interactive ? `<td><div class="action-row"><button class="button ghost" data-edit-account="${escapeHtml(account.id)}">编辑</button><button class="button ghost" data-delete-account="${escapeHtml(account.id)}">删除</button></div></td>` : ""}
    </tr>`).join("");
  $(selector).innerHTML = `<table><thead><tr><th>渠道</th><th>供应商</th><th>模型</th><th>状态</th><th>并发</th><th>优先级</th><th>权重</th><th>剩余额度</th>${interactive ? "<th>操作</th>" : ""}</tr></thead><tbody>${rows || `<tr><td class="empty" colspan="${interactive ? 9 : 8}">暂无渠道，请先添加上游渠道</td></tr>`}</tbody></table>`;
};

const selectModel = async (model) => {
  await routing.select(model);
  setView("routing");
};

const resetChannelForm = () => {
  channelForm.reset();
  state.editingAccount = null;
  state.selectedModels = [];
  state.validation = null;
  $("#form-mode").value = "create";
  $("#account-id").disabled = false;
  $("#api-key").required = true;
  $("#validation-actions").hidden = false;
  $("#channel-dialog-title").textContent = "添加上游渠道";
  $("#validation-message").textContent = "";
  populateProviders();
  renderSelectedModels();
};

const populateProviders = () => {
  const select = $("#provider-id");
  select.innerHTML = state.manifests.map((item) => `<option value="${escapeHtml(item.provider_id)}">${escapeHtml(item.display_name)}</option>`).join("");
  const manifest = state.manifests.find((item) => item.provider_id === select.value) ?? state.manifests[0];
  if (manifest) $("#api-base").value = manifest.default_api_base;
};

const openCreateDialog = () => {
  resetChannelForm();
  channelDialog.showModal();
};

const priorityValue = (value) => value >= 400 ? "400" : value >= 300 ? "300" : value >= 200 ? "200" : "100";

const openEditDialog = (accountId) => {
  const account = state.accounts.find((item) => item.id === accountId);
  if (!account) return;
  resetChannelForm();
  state.editingAccount = account;
  state.selectedModels = account.models.slice();
  $("#form-mode").value = "edit";
  $("#channel-dialog-title").textContent = "编辑渠道调度参数";
  $("#account-id").value = account.id;
  $("#account-id").disabled = true;
  $("#display-name").value = account.display_name;
  $("#group").value = account.group ?? "";
  $("#api-base").value = account.base_url_display;
  $("#api-key").required = false;
  $("#provider-id").innerHTML = `<option value="${escapeHtml(account.provider)}">${escapeHtml(account.provider)}</option>`;
  $("#provider-id").disabled = true;
  $("#priority").value = priorityValue(account.priority);
  $("#weight").value = account.weight;
  $("#max-concurrency").value = account.max_concurrency ?? account.runtime.max_concurrency;
  $("#enabled").checked = account.runtime.enabled;
  $("#quota-total").value = account.quotas?.total ?? "";
  $("#quota-five-hour").value = account.quotas?.five_hour ?? "";
  $("#quota-weekly").value = account.quotas?.weekly ?? "";
  $("#validation-actions").hidden = true;
  renderSelectedModels();
  channelDialog.showModal();
};

const numericOrNull = (selector) => {
  const value = $(selector).value.trim();
  return value === "" ? null : Number(value);
};

const addModel = (model = $("#model-input").value) => {
  const normalized = model.trim();
  if (!normalized || state.selectedModels.includes(normalized)) return;
  state.selectedModels = [...state.selectedModels, normalized];
  $("#model-input").value = "";
  renderSelectedModels();
};

const renderSelectedModels = () => {
  $("#selected-models").innerHTML = state.selectedModels.map((model) => `<span class="chip">${escapeHtml(model)}<button type="button" data-remove-model="${escapeHtml(model)}" aria-label="移除 ${escapeHtml(model)}">×</button></span>`).join("");
};

const validateChannel = async () => {
  const apiKey = $("#api-key").value.trim();
  const apiBase = $("#api-base").value.trim();
  if (!apiKey || !apiBase) {
    showNotice("请填写 API Base URL 和 API Key", true);
    return;
  }
  const button = $("#validate-channel-button");
  button.disabled = true;
  button.textContent = "正在校验";
  try {
    const result = await api.validateProvider({
      provider_id: $("#provider-id").value,
      api_base: apiBase,
      api_key: apiKey,
      group: $("#group").value.trim() || null,
    });
    state.validation = result;
    $("#validation-message").textContent = result.message;
    if (!result.ok) {
      showNotice(result.message, true);
      return;
    }
    $("#api-base").value = result.normalized_api_base;
    const discovered = result.models.map((item) => item.model);
    state.selectedModels = [...new Set([...state.selectedModels, ...discovered])];
    $("#model-options").innerHTML = discovered.map((model) => `<option value="${escapeHtml(model)}"></option>`).join("");
    renderSelectedModels();
    showNotice(result.message);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "校验并获取模型";
  }
};

const accountPayload = () => {
  const editing = state.editingAccount;
  const manifest = state.manifests.find((item) => item.provider_id === $("#provider-id").value);
  const prefix = manifest?.litellm_provider_prefix ?? editing?.provider ?? $("#provider-id").value;
  const existing = new Map((editing?.deployments ?? []).map((item) => [item.public_model, item]));
  const deployments = state.selectedModels.map((model) => {
    const current = existing.get(model);
    return current ? {
      public_model: current.public_model,
      provider_model: current.provider_model,
      litellm_model_id: current.litellm_model_id,
      enabled: current.enabled,
    } : { public_model: model, provider_model: `${prefix}/${model}`, enabled: true };
  });
  const apiKey = $("#api-key").value.trim();
  return {
    id: editing?.id ?? $("#account-id").value.trim(),
    display_name: $("#display-name").value.trim(),
    provider: prefix,
    group: $("#group").value.trim() || null,
    base_url_display: $("#api-base").value.trim(),
    enabled: $("#enabled").checked,
    max_concurrency: Number($("#max-concurrency").value),
    priority: Number($("#priority").value),
    weight: Number($("#weight").value),
    quotas: { unit: editing?.quotas?.unit ?? "tokens", total: numericOrNull("#quota-total"), five_hour: numericOrNull("#quota-five-hour"), weekly: numericOrNull("#quota-weekly") },
    deployments,
    ...(apiKey ? { api_key: apiKey } : {}),
  };
};

const saveChannel = async (event) => {
  event.preventDefault();
  if (state.selectedModels.length === 0) {
    showNotice("请至少选择或输入一个模型", true);
    return;
  }
  if (!state.editingAccount && !state.validation?.ok) {
    showNotice("请先校验上游渠道", true);
    return;
  }
  const button = $("#save-channel-button");
  button.disabled = true;
  try {
    const payload = accountPayload();
    const result = state.editingAccount ? await api.updateAccount(state.editingAccount.id, payload) : await api.createAccount(payload);
    showNotice(result.message, !result.ok);
    if (!result.ok) return;
    channelDialog.close();
    await refresh(true);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
};

const openDeleteDialog = (accountId) => {
  const account = state.accounts.find((item) => item.id === accountId);
  if (!account) return;
  deleteDialog.dataset.accountId = accountId;
  $("#delete-message").textContent = `将删除“${account.display_name}”及由号池创建的 LiteLLM Deployment。`;
  deleteDialog.showModal();
};

const confirmDelete = async (event) => {
  event.preventDefault();
  const accountId = deleteDialog.dataset.accountId;
  deleteDialog.close();
  try {
    const result = await api.deleteAccount(accountId);
    showNotice(result.message, !result.ok);
    await refresh(true);
  } catch (error) {
    showNotice(error.message, true);
  }
};

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.view) setView(target.dataset.view);
  if (target.dataset.selectModel) selectModel(target.dataset.selectModel).catch((error) => showNotice(error.message, true));
  if (target.dataset.editAccount) openEditDialog(target.dataset.editAccount);
  if (target.dataset.deleteAccount) openDeleteDialog(target.dataset.deleteAccount);
  if (target.dataset.removeModel) {
    state.selectedModels = state.selectedModels.filter((model) => model !== target.dataset.removeModel);
    renderSelectedModels();
  }
  if (target.hasAttribute("data-close-dialog")) channelDialog.close();
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setToken($("#admin-token").value.trim());
  $("#login-error").textContent = "";
  try {
    await loadDashboard();
    showApp();
  } catch (error) {
    showLogin(error.message);
  }
});

$("#logout-button").addEventListener("click", () => showLogin());
$("#refresh-button").addEventListener("click", () => refresh());
$("#add-channel-button").addEventListener("click", openCreateDialog);
$("#validate-channel-button").addEventListener("click", validateChannel);
$("#add-model-button").addEventListener("click", () => addModel());
$("#model-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addModel(); }
});
$("#provider-id").addEventListener("change", (event) => {
  const manifest = state.manifests.find((item) => item.provider_id === event.target.value);
  if (manifest) $("#api-base").value = manifest.default_api_base;
  state.validation = null;
});
channelForm.addEventListener("submit", saveChannel);
$("#confirm-delete").addEventListener("click", confirmDelete);
window.addEventListener("account-pool:unauthorized", () => showLogin("LiteLLM 管理令牌无效或已过期"));

if (getToken()) refresh(true);
else showLogin();

window.setInterval(() => {
  if (!appView.hidden && !document.hidden && !channelDialog.open) refresh(true);
}, 10_000);
