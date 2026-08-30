// 本文件装配 4100 调度控制台，管理渠道、模型策略和实时路由状态。
import { api, clearToken, getToken, setToken } from "./api.js?v=6";
import { escapeHtml, formatNumber, priorityName, statusBadge, strategyNames } from "./format.js?v=5";
import { createRoutingWorkbench } from "./routing.js?v=5";

const state = {
  channels: [],
  models: [],
  stats: null,
  status: null,
  upstreamProviders: [],
  selectedModels: [],
  discoveredModels: [],
  discovery: null,
  editingChannel: null,
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
  const [channelList, overview, models, stats, status, upstreamProviders] = await Promise.all([
    api.channels(), api.overview(), api.models(), api.stats(), api.litellmStatus(), api.upstreamProviders(),
  ]);
  const runtimeByChannel = new Map(overview.channels.map((channel) => [channel.channel_id, channel.runtime]));
  const channels = channelList.channels.map((channel) => ({
    ...channel,
    id: channel.channel_id,
    runtime: runtimeByChannel.get(channel.channel_id) ?? null,
  }));
  Object.assign(state, { channels, models, stats, status, upstreamProviders });
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
  const rows = state.channels.map((channel) => `
    <tr>
      <td><strong>${escapeHtml(channel.display_name)}</strong><div class="muted">${escapeHtml(channel.group || channel.channel_id)}</div></td>
      <td>${escapeHtml(channel.provider)}</td>
      <td>${modelTags(channel.models)}</td>
      <td>${statusBadge(channel.runtime?.health ?? (channel.administrative_state === "enabled" ? "unknown" : "disabled"))}</td>
      <td>${channel.runtime?.inflight ?? 0} / ${channel.runtime?.max_concurrency ?? channel.max_concurrency}</td>
      <td>${priorityName(channel.priority)} <span class="muted">(${channel.priority})</span></td>
      <td>${channel.weight}</td>
      <td>${formatNumber(channel.runtime?.quota?.total)}</td>
      ${interactive ? `<td><div class="action-row"><button class="button ghost" data-edit-channel="${escapeHtml(channel.channel_id)}">编辑</button><button class="button ghost" data-delete-channel="${escapeHtml(channel.channel_id)}">删除</button></div></td>` : ""}
    </tr>`).join("");
  $(selector).innerHTML = `<table><thead><tr><th>渠道</th><th>转发协议</th><th>模型</th><th>状态</th><th>并发</th><th>优先级</th><th>权重</th><th>剩余额度</th>${interactive ? "<th>操作</th>" : ""}</tr></thead><tbody>${rows || `<tr><td class="empty" colspan="${interactive ? 9 : 8}">暂无渠道，请先添加上游渠道</td></tr>`}</tbody></table>`;
};

const selectModel = async (model) => {
  await routing.select(model);
  setView("routing");
};

const resetChannelForm = () => {
  channelForm.reset();
  state.editingChannel = null;
  state.selectedModels = [];
  state.discoveredModels = [];
  state.discovery = null;
  $("#form-mode").value = "create";
  $("#account-id").disabled = false;
  $("#provider-id").disabled = false;
  $("#forwarding-provider").disabled = false;
  $("#forwarding-provider").value = "openai";
  $("#advanced-settings").open = false;
  $("#api-key").required = true;
  $("#discovery-actions").hidden = false;
  $("#channel-dialog-title").textContent = "添加上游渠道";
  $("#discovery-message").textContent = "";
  populateProviders();
  renderSelectedModels();
};

const populateProviders = () => {
  const select = $("#provider-id");
  select.innerHTML = state.upstreamProviders.map((item) => `<option value="${escapeHtml(item.provider_id)}">${escapeHtml(item.display_name)}</option>`).join("");
  const manifest = state.upstreamProviders.find((item) => item.provider_id === select.value) ?? state.upstreamProviders[0];
  if (manifest) $("#api-base").value = manifest.default_api_base;
};

const openCreateDialog = () => {
  resetChannelForm();
  channelDialog.showModal();
};

const priorityValue = (value) => value >= 400 ? "400" : value >= 300 ? "300" : value >= 200 ? "200" : "100";
const normalizeForwardingProvider = (value) => ({
  openai_compatible: "openai",
  new_api: "openai",
  glm_official: "zai",
  lmu_static_metadata: "openai",
}[value] ?? value);

const openEditDialog = async (channelId) => {
  const channel = await api.channel(channelId);
  resetChannelForm();
  state.editingChannel = channel;
  state.selectedModels = channel.bindings.filter((item) => item.enabled).map((item) => item.public_model);
  state.discoveredModels = channel.bindings
    .filter((item) => item.enabled && item.provider_model)
    .map((item) => item.public_model);
  $("#form-mode").value = "edit";
  $("#channel-dialog-title").textContent = "编辑渠道调度参数";
  $("#account-id").value = channel.channel_id;
  $("#account-id").disabled = true;
  $("#display-name").value = channel.display_name;
  $("#group").value = channel.group ?? "";
  $("#api-base").value = channel.base_url_display;
  $("#api-key").required = false;
  const discoveryProvider = channel.model_discovery_provider_id ?? "openai_compatible";
  if ([...$("#provider-id").options].some((option) => option.value === discoveryProvider)) {
    $("#provider-id").value = discoveryProvider;
  }
  const forwardingProvider = normalizeForwardingProvider(channel.provider);
  if (![...$("#forwarding-provider").options].some((option) => option.value === forwardingProvider)) {
    $("#forwarding-provider").insertAdjacentHTML(
      "beforeend",
      `<option value="${escapeHtml(forwardingProvider)}">${escapeHtml(forwardingProvider)}</option>`,
    );
  }
  $("#forwarding-provider").value = forwardingProvider;
  $("#advanced-settings").open = true;
  $("#priority").value = priorityValue(channel.priority);
  $("#weight").value = channel.weight;
  $("#max-concurrency").value = channel.max_concurrency;
  $("#enabled").checked = channel.administrative_state === "enabled";
  $("#quota-total").value = channel.quotas?.total ?? "";
  $("#quota-five-hour").value = channel.quotas?.five_hour ?? "";
  $("#quota-weekly").value = channel.quotas?.weekly ?? "";
  $("#discovery-actions").hidden = false;
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

const discoverModels = async () => {
  const apiKey = $("#api-key").value.trim();
  const apiBase = $("#api-base").value.trim();
  if (!apiKey || !apiBase) {
    showNotice("请填写 API Base URL 和 API Key", true);
    return;
  }
  const button = $("#discover-models-button");
  button.disabled = true;
  button.textContent = "正在获取";
  try {
    const result = await api.discoverUpstreamModels({
      provider_id: $("#provider-id").value,
      api_base: apiBase,
      api_key: apiKey,
    });
    state.discovery = result;
    $("#discovery-message").textContent = result.message;
    if (!result.ok) {
      showNotice(result.message, true);
      return;
    }
    $("#api-base").value = result.normalized_api_base;
    state.discoveredModels = result.models;
    const discovered = result.models;
    state.selectedModels = [...new Set([...state.selectedModels, ...discovered])];
    $("#model-options").innerHTML = discovered.map((model) => `<option value="${escapeHtml(model)}"></option>`).join("");
    renderSelectedModels();
    showNotice(result.message);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "从资源侧获取模型";
  }
};

const channelPayload = () => {
  const editing = state.editingChannel;
  const provider = $("#forwarding-provider").value;
  const existing = new Map((editing?.bindings ?? []).map((item) => [item.public_model, item]));
  const discoveredBindings = new Map(state.discoveredModels.map((model) => [model, `${provider}/${model}`]));
  const bindings = state.selectedModels.map((model) => {
    const current = existing.get(model);
    return current ? {
      binding_id: current.binding_id,
      public_model: current.public_model,
      provider_model: current.provider_model,
      litellm_deployment_id: current.litellm_deployment_id,
      ownership: current.ownership,
      enabled: current.enabled,
    } : {
      binding_id: null,
      public_model: model,
      provider_model: discoveredBindings.get(model) ?? `${provider}/${model}`,
      litellm_deployment_id: null,
      ownership: "pool_managed",
      enabled: true,
    };
  });
  const apiKey = $("#api-key").value.trim();
  return {
    ...(!editing ? { legacy_account_id: $("#account-id").value.trim() } : {}),
    display_name: $("#display-name").value.trim(),
    provider,
    model_discovery_provider_id: $("#provider-id").value,
    group: $("#group").value.trim() || null,
    base_url_display: $("#api-base").value.trim(),
    administrative_state: $("#enabled").checked ? "enabled" : "disabled",
    max_concurrency: Number($("#max-concurrency").value),
    priority: Number($("#priority").value),
    weight: Number($("#weight").value),
    quotas: {
      unit: editing?.quotas?.unit ?? "tokens",
      total: numericOrNull("#quota-total"),
      five_hour: numericOrNull("#quota-five-hour"),
      weekly: numericOrNull("#quota-weekly"),
    },
    bindings,
    ...(apiKey ? { api_key: apiKey } : {}),
  };
};

const operationMessage = (operation, action) => {
  if (operation.failure?.message) return operation.failure.message;
  if (operation.requires_key) return `${action}需要重新提供 API Key`;
  return `${action}已提交，状态：${operation.operation_status}`;
};

const saveChannel = async (event) => {
  event.preventDefault();
  if (state.selectedModels.length === 0) {
    showNotice("请至少选择或输入一个模型", true);
    return;
  }
  if (!state.editingChannel && !state.discovery?.ok) {
    showNotice("请先从资源侧获取模型", true);
    return;
  }
  const button = $("#save-channel-button");
  button.disabled = true;
  try {
    const payload = channelPayload();
    const result = state.editingChannel
      ? await api.updateChannel(state.editingChannel.channel_id, payload)
      : await api.createChannel(payload);
    const failed = result.operation_status === "failed";
    showNotice(operationMessage(result, state.editingChannel ? "渠道更新" : "渠道创建"), failed);
    if (failed) return;
    channelDialog.close();
    await refresh(true);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
};

const openDeleteDialog = (channelId) => {
  const channel = state.channels.find((item) => item.channel_id === channelId);
  if (!channel) return;
  deleteDialog.dataset.channelId = channelId;
  $("#delete-message").textContent = `将删除“${channel.display_name}”及由号池创建的 LiteLLM Deployment。`;
  deleteDialog.showModal();
};

const confirmDelete = async (event) => {
  event.preventDefault();
  const channelId = deleteDialog.dataset.channelId;
  deleteDialog.close();
  try {
    const result = await api.deleteChannel(channelId);
    const failed = result.operation_status === "failed";
    showNotice(operationMessage(result, "渠道删除"), failed);
    if (failed) return;
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
  if (target.dataset.editChannel) openEditDialog(target.dataset.editChannel).catch((error) => showNotice(error.message, true));
  if (target.dataset.deleteChannel) openDeleteDialog(target.dataset.deleteChannel);
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
$("#discover-models-button").addEventListener("click", discoverModels);
$("#add-model-button").addEventListener("click", () => addModel());
$("#model-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addModel(); }
});
$("#provider-id").addEventListener("change", (event) => {
  const manifest = state.upstreamProviders.find((item) => item.provider_id === event.target.value);
  if (manifest) $("#api-base").value = manifest.default_api_base;
  state.discovery = null;
  state.discoveredModels = [];
  state.selectedModels = [];
  renderSelectedModels();
});
channelForm.addEventListener("submit", saveChannel);
$("#confirm-delete").addEventListener("click", confirmDelete);
window.addEventListener("account-pool:unauthorized", () => showLogin("LiteLLM 管理令牌无效或已过期"));

if (getToken()) refresh(true);
else showLogin();

window.setInterval(() => {
  if (!appView.hidden && !document.hidden && !channelDialog.open) refresh(true);
}, 10_000);
