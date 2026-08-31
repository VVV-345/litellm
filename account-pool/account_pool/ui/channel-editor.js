// 本文件封装渠道编辑弹窗，独立管理模型发现、表单状态和渠道保存流程。
import { escapeHtml, formatChannelOperationMessage } from "./format.js?v=6";

const $ = (selector) => document.querySelector(selector);

const priorityValue = (value) => value >= 400 ? "400" : value >= 300 ? "300" : value >= 200 ? "200" : "100";

const normalizeForwardingProvider = (value) => ({
  openai_compatible: "openai",
  new_api: "openai",
  glm_official: "zai",
  lmu_static_metadata: "openai",
}[value] ?? value);

const numericOrNull = (selector) => {
  const value = $(selector).value.trim();
  return value === "" ? null : Number(value);
};

export const createChannelEditor = ({ api, getUpstreamProviders, showNotice, refreshDashboard }) => {
  const state = {
    editingChannel: null,
    selectedModels: [],
    discoveredModels: [],
    discovery: null,
  };
  const dialog = $("#channel-dialog");
  const form = $("#channel-form");

  const renderSelectedModels = () => {
    $("#selected-models").innerHTML = state.selectedModels
      .map((model) => `<span class="chip">${escapeHtml(model)}<button type="button" data-remove-model="${escapeHtml(model)}" aria-label="移除 ${escapeHtml(model)}">×</button></span>`)
      .join("");
  };

  const populateProviders = () => {
    const select = $("#provider-id");
    const providers = getUpstreamProviders();
    select.innerHTML = providers
      .map((item) => `<option value="${escapeHtml(item.provider_id)}">${escapeHtml(item.display_name)}</option>`)
      .join("");
    const manifest = providers.find((item) => item.provider_id === select.value) ?? providers[0];
    if (manifest) $("#api-base").value = manifest.default_api_base;
  };

  const reset = () => {
    form.reset();
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

  const addModel = (model = $("#model-input").value) => {
    const normalized = model.trim();
    if (!normalized || state.selectedModels.includes(normalized)) return;
    state.selectedModels = [...state.selectedModels, normalized];
    $("#model-input").value = "";
    renderSelectedModels();
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
      state.selectedModels = [...new Set([...state.selectedModels, ...result.models])];
      $("#model-options").innerHTML = result.models
        .map((model) => `<option value="${escapeHtml(model)}"></option>`)
        .join("");
      renderSelectedModels();
      showNotice(result.message);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "从资源侧获取模型";
    }
  };

  const payload = () => {
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

  const save = async (event) => {
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
      const result = state.editingChannel
        ? await api.updateChannel(state.editingChannel.channel_id, payload())
        : await api.createChannel(payload());
      const failed = result.operation_status === "failed";
      showNotice(formatChannelOperationMessage(result, state.editingChannel ? "渠道更新" : "渠道创建"), failed);
      if (failed) return;
      dialog.close();
      await refreshDashboard(true);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  const openCreate = () => {
    reset();
    dialog.showModal();
  };

  const openEdit = async (channelId) => {
    const channel = await api.channel(channelId);
    reset();
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
    renderSelectedModels();
    dialog.showModal();
  };

  const handleButton = (target) => {
    if (target.dataset.removeModel) {
      state.selectedModels = state.selectedModels.filter((model) => model !== target.dataset.removeModel);
      renderSelectedModels();
      return true;
    }
    if (target.hasAttribute("data-close-dialog")) {
      dialog.close();
      return true;
    }
    return false;
  };

  form.addEventListener("submit", save);
  $("#discover-models-button").addEventListener("click", discoverModels);
  $("#add-model-button").addEventListener("click", () => addModel());
  $("#model-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addModel();
    }
  });
  $("#provider-id").addEventListener("change", (event) => {
    const manifest = getUpstreamProviders().find((item) => item.provider_id === event.target.value);
    if (manifest) $("#api-base").value = manifest.default_api_base;
    state.discovery = null;
    state.discoveredModels = [];
    state.selectedModels = [];
    renderSelectedModels();
  });

  return { handleButton, isOpen: () => dialog.open, openCreate, openEdit };
};
