// 本文件装配 4100 调度控制台，管理渠道、模型策略和实时路由状态。
import { api, clearToken, getToken, setToken } from "./api.js?v=7";
import { createChannelEditor } from "./channel-editor.js?v=1";
import {
  escapeHtml,
  formatChannelOperationMessage,
  formatNumber,
  priorityName,
  statusBadge,
  strategyNames,
} from "./format.js?v=6";
import { createRoutingWorkbench } from "./routing.js?v=8";

const state = {
  channels: [],
  models: [],
  stats: null,
  status: null,
  upstreamProviders: [],
};

const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const appView = $("#app-view");
const notice = $("#notice");
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
const channelEditor = createChannelEditor({
  api,
  getUpstreamProviders: () => state.upstreamProviders,
  showNotice,
  refreshDashboard: refresh,
});

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
    showNotice(formatChannelOperationMessage(result, "渠道删除"), failed);
    if (failed) return;
    await refresh(true);
  } catch (error) {
    showNotice(error.message, true);
  }
};

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (channelEditor.handleButton(target)) return;
  if (target.dataset.view) setView(target.dataset.view);
  if (target.dataset.selectModel) selectModel(target.dataset.selectModel).catch((error) => showNotice(error.message, true));
  if (target.dataset.editChannel) channelEditor.openEdit(target.dataset.editChannel).catch((error) => showNotice(error.message, true));
  if (target.dataset.deleteChannel) openDeleteDialog(target.dataset.deleteChannel);
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
$("#add-channel-button").addEventListener("click", channelEditor.openCreate);
$("#confirm-delete").addEventListener("click", confirmDelete);
window.addEventListener("account-pool:unauthorized", () => showLogin("LiteLLM 管理令牌无效或已过期"));

if (getToken()) refresh(true);
else showLogin();

window.setInterval(() => {
  if (!appView.hidden && !document.hidden && !channelEditor.isOpen()) refresh(true);
}, 10_000);
