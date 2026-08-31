// 本文件实现 4100 调度工作台的模型选择、策略版本和候选人工覆盖交互。
import { api } from "./api.js?v=7";
import {
  escapeHtml,
  formatNumber,
  formatPercent,
  formatTime,
  healthNames,
  priorityName,
  strategyNames,
  strategyOptions,
} from "./format.js?v=7";

const reasonNames = {
  available: "当前可调度",
  capacity: "并发已满",
  cooldown: "冷却中",
  credential_invalid: "凭证无效",
  deployment_disabled: "绑定已停用",
  manual_pause: "模型绑定已暂停",
  model_not_found: "上游模型不存在",
  rate_limited: "上游限流",
  rate_limit_unknown: "限流恢复时间未知",
  subscription_balance_exhausted: "套餐额度已用尽",
  unhealthy: "健康检查未通过",
  upstream_unavailable: "上游暂不可用",
};

const sourceNames = {
  administrative: "人工配置",
  health: "健康检测",
  restriction: "冷却限制",
  capacity: "运行容量",
  quota: "额度窗口",
  runtime: "运行状态",
};

const billingModeNames = {
  subscription: "套餐",
  metered: "按量",
  provider_decided: "厂商决定",
};

const quotaUnitNames = {
  requests: "次",
  tokens: "tokens",
  credits: "积分",
  currency: "货币",
  provider_units: "用量",
};

const sortReasonNames = {
  channel_priority: "渠道优先级",
  random: "请求级随机",
  latency: "延迟",
  remaining_quota_ratio: "剩余额度",
  effective_cost: "有效成本",
  inflight_ratio: "并发占用",
  weighted_round_robin: "权重轮询",
  stable_id: "稳定 ID",
};

const costText = (route) => {
  const evidence = route.cost_evidence;
  if (!evidence) return "未知";
  if (evidence.kind === "subscription_included") return "套餐内包含";
  const suffix = evidence.partial ? "（部分价格）" : "";
  return `${formatNumber(evidence.effective_cost)} ${escapeHtml(evidence.currency)}/${escapeHtml(evidence.unit)}${suffix}`;
};

const packageQuotaText = (route) => {
  if (route.billing_mode !== "subscription") return null;
  if (route.remaining_quota !== null && Number(route.remaining_quota) <= 0) return "套餐额度已用尽";
  if (route.remaining_quota !== null && route.remaining_quota_unit !== null) {
    const unit = quotaUnitNames[route.remaining_quota_unit] ?? route.remaining_quota_unit;
    return `套餐余量 ${formatNumber(route.remaining_quota)} ${unit}`;
  }
  return null;
};

const availabilityText = (route) => {
  if (route.available) return '<span class="badge health-healthy">可调度</span>';
  const reason = reasonNames[route.reason_code] ?? route.reason_code ?? route.unavailable_reason ?? "不可用";
  const source = sourceNames[route.exclusion_source];
  return `<span class="badge health-disabled">${escapeHtml(reason)}</span>${source ? `<div class="muted route-detail">${escapeHtml(source)} · ${escapeHtml(route.exclusion_scope ?? "channel")}</div>` : ""}${route.retry_at ? `<div class="muted route-detail">恢复：${escapeHtml(formatTime(route.retry_at))}</div>` : ""}`;
};

const sortReasonText = (route) => (route.sort_reason_codes ?? [])
  .map((reason) => sortReasonNames[reason] ?? reason)
  .join(" / ") || "稳定顺序";

const candidateControls = (route, policyAvailable, orderDirty) => route.binding_id && policyAvailable && !orderDirty
  ? `<button class="button ghost" type="button" data-edit-route="${escapeHtml(route.binding_id)}">调整</button>`
  : `<span class="muted">${orderDirty ? "请先保存顺序" : route.binding_id ? "只读" : "无绑定 ID"}</span>`;

export const createRoutingWorkbench = ({ showNotice, refreshDashboard }) => {
  let models = [];
  let selectedModel = null;
  let routes = [];
  let policy = null;
  let policyAvailable = true;
  let editingBindingId = null;
  let draftOrder = [];
  let draggingBindingId = null;

  const routeDialog = document.querySelector("#route-dialog");
  const routeForm = document.querySelector("#route-form");

  const loadSelected = async () => {
    if (!selectedModel) {
      routes = [];
      policy = null;
      return;
    }
    const [loadedRoutes, loadedPolicy] = await Promise.all([
      api.routingTable(selectedModel),
      api.routingPolicy(selectedModel).catch((error) => {
        if (error.status === 503) return null;
        throw error;
      }),
    ]);
    routes = loadedRoutes;
    policy = loadedPolicy;
    policyAvailable = loadedPolicy !== null;
    draftOrder = [];
    draggingBindingId = null;
  };

  const sourceOrder = () => routes.flatMap((route) => route.binding_id ? [route.binding_id] : []);

  const hasCompleteBindingOrder = () => routes.length > 0 && sourceOrder().length === routes.length;

  const orderedRoutes = () => {
    if (draftOrder.length === 0) return routes;
    const byBindingId = new Map(routes.map((route) => [route.binding_id, route]));
    const fromDraft = draftOrder.flatMap((bindingId) => {
      const route = byBindingId.get(bindingId);
      return route ? [route] : [];
    });
    const known = new Set(draftOrder);
    return [...fromDraft, ...routes.filter((route) => !known.has(route.binding_id))];
  };

  const orderDirty = () => {
    const source = sourceOrder();
    return draftOrder.length > 0 && (draftOrder.length !== source.length || draftOrder.some((bindingId, index) => bindingId !== source[index]));
  };

  const canSaveOrder = () => Boolean(policy && policyAvailable && hasCompleteBindingOrder() && orderDirty());

  const renderModelList = () => {
    const dirty = orderDirty();
    document.querySelector("#model-list").innerHTML = models.map((model) => `
      <button class="model-item ${model.model === selectedModel ? "active" : ""}" data-select-model="${escapeHtml(model.model)}" ${dirty ? "disabled title=\"请先保存拖拽顺序\"" : ""}>
        <strong>${escapeHtml(model.model)}</strong>
        <span>${escapeHtml(strategyNames[model.strategy] ?? model.strategy)} · ${model.available_accounts}/${model.accounts} 可用</span>
      </button>`).join("") || '<div class="empty">尚未配置模型</div>';
  };

  const renderRouteTable = () => {
    const model = models.find((item) => item.model === selectedModel);
    if (!model) {
      document.querySelector("#route-header").innerHTML = "<div><h2>路由顺序</h2><p>添加渠道后可配置模型调度</p></div>";
      document.querySelector("#route-table").innerHTML = '<div class="empty">暂无路由</div>';
      document.querySelector("#route-policy-warning").hidden = true;
      return;
    }
    const activeStrategy = policy?.strategy ?? model.strategy;
    const dirty = orderDirty();
    const canReorder = Boolean(policy && policyAvailable && hasCompleteBindingOrder());
    const options = strategyOptions.map(([value, label]) => `<option value="${value}" ${activeStrategy === value ? "selected" : ""}>${label}</option>`).join("");
    const version = policy?.version ?? model.version ?? 0;
    const dynamic = routes.some((route) => route.dynamic_order);
    document.querySelector("#route-header").innerHTML = `
      <div><h2>${escapeHtml(model.model)}</h2><p>版本 ${version}${dynamic ? " · 动态策略预览不推进真实调度序列" : ""}</p></div>
      <div class="route-controls">${dirty ? '<button id="save-route-order" class="button primary" type="button" ' + (canSaveOrder() ? "" : "disabled") + ">保存顺序</button>" : ""}<label class="muted" for="strategy-select">调度策略</label><select id="strategy-select" ${policyAvailable && !dirty ? "" : "disabled"}>${options}</select></div>`;
    const rows = orderedRoutes().map((route, index) => {
      const canDrag = canReorder && route.binding_id;
      const packageQuota = packageQuotaText(route);
      return `
      <tr class="${draggingBindingId === route.binding_id ? "routing-dragging" : ""}" data-route-drop="${canDrag ? escapeHtml(route.binding_id) : ""}">
        <td>${canDrag ? `<button class="drag-handle" type="button" draggable="true" data-drag-route="${escapeHtml(route.binding_id)}" title="拖拽调整顺序，保存后生效" aria-label="拖拽调整顺序，保存后生效">&#x2261;</button>` : ""}<strong>${index + 1}</strong><div class="muted">${escapeHtml(sortReasonText(route))}</div></td>
        <td><strong>${escapeHtml(route.display_name)}</strong><div class="muted route-detail">${escapeHtml(route.account_id)}</div><div class="muted route-detail">${escapeHtml(route.deployment_id)}</div></td>
        <td><span class="badge health-${escapeHtml(route.health)}">${escapeHtml(healthNames[route.health] ?? route.health)}</span><div class="muted route-detail">${escapeHtml(billingModeNames[route.billing_mode] ?? route.billing_mode)}</div></td>
        <td>${route.inflight} / ${route.max_concurrency}<div class="muted route-detail">额度 ${formatPercent(route.remaining_quota_ratio)}</div>${packageQuota ? `<div class="muted route-detail">${escapeHtml(packageQuota)}</div>` : ""}</td>
        <td>${route.latency_ewma_ms == null ? "未知" : `${formatNumber(route.latency_ewma_ms)} ms`}<div class="muted route-detail">${costText(route)}</div></td>
        <td>${priorityName(route.priority)}<div class="muted route-detail">权重 ${route.effective_weight}</div></td>
        <td>${availabilityText(route)}</td>
        <td>${candidateControls(route, policyAvailable, dirty)}</td>
      </tr>`;
    }).join("");
    document.querySelector("#route-table").innerHTML = `<table><thead><tr><th>顺序与依据</th><th>渠道与绑定</th><th>状态与计费</th><th>并发与额度</th><th>延迟与成本</th><th>人工设置</th><th>资格</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td class="empty" colspan="8">此模型暂无路由</td></tr>'}</tbody></table>`;
    document.querySelector("#strategy-select")?.addEventListener("change", updateStrategy);
    bindRouteDragAndDrop();
    if (!policyAvailable) {
      document.querySelector("#route-policy-warning").hidden = false;
    } else {
      document.querySelector("#route-policy-warning").hidden = true;
    }
  };

  const render = () => {
    renderModelList();
    renderRouteTable();
  };

  const sync = async (nextModels, preferredModel = selectedModel) => {
    models = nextModels;
    selectedModel = models.some((item) => item.model === preferredModel) ? preferredModel : models[0]?.model ?? null;
    await loadSelected();
    render();
  };

  const select = async (model) => {
    if (orderDirty()) {
      showNotice("请先保存拖拽顺序", true);
      return;
    }
    selectedModel = model;
    await loadSelected();
    render();
  };

  async function updateStrategy(event) {
    if (!policy || !selectedModel || orderDirty()) return;
    try {
      policy = await api.updateRoutingPolicy(selectedModel, {
        expected_version: policy.version,
        strategy: event.target.value,
      });
      await refreshDashboard(true);
      showNotice("调度策略已更新");
    } catch (error) {
      showNotice(error.message === "version_conflict" ? "策略已被其他管理员修改，请刷新后重试" : error.message, true);
      await sync(models, selectedModel);
    }
  }

  const openCandidate = (bindingId) => {
    if (orderDirty()) {
      showNotice("请先保存拖拽顺序", true);
      return;
    }
    const route = routes.find((item) => item.binding_id === bindingId);
    if (!route || !policy) return;
    editingBindingId = bindingId;
    document.querySelector("#route-dialog-title").textContent = route.display_name;
    document.querySelector("#route-dialog-description").textContent = `${route.account_id} · ${route.deployment_id}`;
    document.querySelector("#route-weight").value = policy.overrides.find((item) => item.binding_id === bindingId)?.weight ?? "";
    document.querySelector("#route-paused").checked = route.routing_paused;
    routeDialog.showModal();
  };

  const saveCandidate = async (event) => {
    event.preventDefault();
    if (!policy || !selectedModel || !editingBindingId) return;
    try {
      policy = await api.updateRoutingCandidate(selectedModel, editingBindingId, {
        expected_version: policy.version,
        weight: document.querySelector("#route-weight").value.trim() === "" ? null : Number(document.querySelector("#route-weight").value),
        paused: document.querySelector("#route-paused").checked,
      });
      routeDialog.close();
      await refreshDashboard(true);
      showNotice("候选设置已更新");
    } catch (error) {
      showNotice(error.message === "version_conflict" ? "候选设置已被其他管理员修改，请刷新后重试" : error.message, true);
      await sync(models, selectedModel);
    }
  };

  const resetCandidate = async () => {
    if (!policy || !selectedModel || !editingBindingId) return;
    try {
      policy = await api.deleteRoutingCandidate(selectedModel, editingBindingId, { expected_version: policy.version });
      routeDialog.close();
      await refreshDashboard(true);
      showNotice("候选已恢复自动设置");
    } catch (error) {
      showNotice(error.message === "version_conflict" ? "候选设置已被其他管理员修改，请刷新后重试" : error.message, true);
      await sync(models, selectedModel);
    }
  };

  const moveRoute = (sourceBindingId, targetBindingId) => {
    const current = draftOrder.length === 0 ? sourceOrder() : draftOrder;
    if (!current.includes(sourceBindingId) || !current.includes(targetBindingId)) return;
    const withoutSource = current.filter((bindingId) => bindingId !== sourceBindingId);
    const targetIndex = withoutSource.indexOf(targetBindingId);
    if (targetIndex === -1) return;
    draftOrder = [...withoutSource.slice(0, targetIndex), sourceBindingId, ...withoutSource.slice(targetIndex)];
    draggingBindingId = null;
    render();
  };

  const bindRouteDragAndDrop = () => {
    for (const handle of document.querySelectorAll("[data-drag-route]")) {
      handle.addEventListener("dragstart", (event) => {
        draggingBindingId = handle.dataset.dragRoute;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggingBindingId);
      });
      handle.addEventListener("dragend", () => {
        draggingBindingId = null;
        render();
      });
    }
    for (const target of document.querySelectorAll("[data-route-drop]")) {
      target.addEventListener("dragover", (event) => {
        if (draggingBindingId) event.preventDefault();
      });
      target.addEventListener("drop", (event) => {
        event.preventDefault();
        const sourceBindingId = event.dataTransfer.getData("text/plain") || draggingBindingId;
        const targetBindingId = target.dataset.routeDrop;
        if (sourceBindingId && targetBindingId && sourceBindingId !== targetBindingId) {
          moveRoute(sourceBindingId, targetBindingId);
        }
        draggingBindingId = null;
      });
    }
  };

  const saveOrder = async () => {
    if (!selectedModel || !policy || !canSaveOrder()) return;
    try {
      policy = await api.updateRoutingOrder(selectedModel, {
        expected_version: policy.version,
        binding_ids: orderedRoutes().map((route) => route.binding_id),
      });
      draftOrder = [];
      await refreshDashboard(true);
      showNotice("路由顺序已保存");
    } catch (error) {
      showNotice(error.message === "version_conflict" ? "路由顺序已被其他管理员修改，请刷新后重试" : error.message, true);
      await sync(models, selectedModel);
    }
  };

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (target?.dataset.editRoute) openCandidate(target.dataset.editRoute);
    if (target?.id === "save-route-order") void saveOrder();
    if (target?.hasAttribute("data-close-route-dialog")) routeDialog.close();
  });
  routeForm.addEventListener("submit", saveCandidate);
  document.querySelector("#reset-route-button").addEventListener("click", resetCandidate);

  return { sync, select, selectedModel: () => selectedModel };
};
