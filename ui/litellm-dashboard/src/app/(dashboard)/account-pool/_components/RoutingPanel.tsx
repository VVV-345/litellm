// 本文件提供 LiteLLM Dashboard 的模型策略、路由顺序和候选覆盖管理界面。
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import {
  accountPoolKeys,
  getRoutingModels,
  getRoutingPolicy,
  getRoutingTable,
  resetRoutingCandidate,
  updateRoutingCandidate,
  updateRoutingPolicy,
} from "../api";
import {
  accountPoolTableColumnDividerClass,
  formatAccountPoolEpoch,
  formatAccountPoolNumber,
  healthPresentation,
} from "../accountPoolPresentation";
import { reasonLabel } from "../reasonLabels";
import CapacityMeter from "./CapacityMeter";
import { AccountPoolPanel, AccountPoolQueryState } from "./AccountPoolPanel";
import { AutoRankingPreviewDialog, CandidateDialog } from "./RoutingDialogs";
import type {
  RoutingCandidateMutation,
  RoutingModelSummary,
  RoutingPolicyState,
  RoutingStrategy,
  RoutingTableEntry,
} from "../types";

const strategyLabels: Record<RoutingStrategy, string> = {
  priority: "手动优先级",
  random: "随机",
  lowest_latency: "延迟优先",
  highest_remaining_quota: "剩余额度优先",
  lowest_effective_cost: "成本最低优先",
  least_inflight: "并发最少优先",
  weighted_round_robin: "权重轮询",
  quota_aware_least_inflight: "额度感知优先",
};

const billingLabels: Record<RoutingTableEntry["billing_mode"], string> = {
  subscription: "套餐",
  metered: "按量",
  provider_decided: "厂商决定",
};

const sortReasonLabels: Record<string, string> = {
  manual_order: "人工顺序",
  priority: "渠道优先级",
  random: "请求级随机",
  latency: "延迟",
  remaining_quota_ratio: "剩余额度",
  effective_cost: "有效成本",
  inflight_ratio: "并发占用",
  weighted_round_robin: "权重轮询",
  stable_id: "稳定 ID",
};

const selectActiveModel = (models: RoutingModelSummary[], selectedModel: string | null): string | null => {
  const selected = selectedModel === null ? undefined : models.find((item) => item.model === selectedModel);
  return selected?.model ?? models[0]?.model ?? null;
};

const costText = (route: RoutingTableEntry): string => {
  const evidence = route.cost_evidence;
  if (!evidence) return "未知";
  if (evidence.kind === "subscription_included") return "套餐内包含";
  return `${formatAccountPoolNumber(evidence.effective_cost)} ${evidence.currency}/${evidence.unit}${evidence.partial ? "（部分）" : ""}`;
};

const sortReasonText = (route: RoutingTableEntry): string =>
  route.sort_reason_codes.map((code) => sortReasonLabels[code] ?? code).join(" / ") || "稳定顺序";

interface RoutingPanelProps {
  accessToken: string;
}

function RoutingPanelState({ loading, error }: { loading: boolean; error: boolean }) {
  if (loading) {
    return <AccountPoolQueryState kind="loading" message="正在读取模型调度表" className="min-h-80" />;
  }
  if (error) {
    return (
      <AccountPoolQueryState
        kind="error"
        message="无法读取模型调度表"
        className="min-h-80 rounded-md border border-destructive/30"
      />
    );
  }
  return <AccountPoolQueryState kind="empty" message="尚未配置模型绑定" className="min-h-40 rounded-md border" />;
}

interface RoutingWorkspaceProps {
  activeModel: string;
  activeSummary: RoutingModelSummary;
  routes: RoutingTableEntry[];
  policy: RoutingPolicyState | undefined;
  loading: boolean;
  routeError: boolean;
  policyError: boolean;
  policyPending: boolean;
  onStrategyChange: (strategy: RoutingStrategy) => void;
  onPreview: () => void;
  onEdit: (route: RoutingTableEntry) => void;
}

function RoutingTableContent({
  routes,
  policy,
  loading,
  routeError,
  onEdit,
}: Pick<RoutingWorkspaceProps, "routes" | "policy" | "loading" | "routeError" | "onEdit">) {
  if (loading) {
    return <AccountPoolQueryState kind="loading" message="正在读取此模型的运行路由" />;
  }
  if (routeError) {
    return <AccountPoolQueryState kind="error" message="无法读取此模型的运行路由，请检查 Account Pool 调度服务" />;
  }
  return (
    <Table className={accountPoolTableColumnDividerClass}>
      <TableHeader>
        <TableRow>
          <TableHead>顺序与依据</TableHead>
          <TableHead>渠道与绑定</TableHead>
          <TableHead>状态与计费</TableHead>
          <TableHead>并发与额度</TableHead>
          <TableHead>延迟与成本</TableHead>
          <TableHead>人工设置</TableHead>
          <TableHead>资格</TableHead>
          <TableHead className="text-right">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {routes.map((route, index) => (
          <TableRow key={`${route.account_id}:${route.deployment_id}:${route.billing_route_id ?? ""}`}>
            <TableCell className="align-top">
              <strong>{route.position ?? index + 1}</strong>
              <span className="mt-1 block max-w-40 whitespace-normal text-xs text-muted-foreground">
                {sortReasonText(route)}
              </span>
            </TableCell>
            <TableCell className="align-top">
              <strong>{route.display_name}</strong>
              <span className="mt-1 block max-w-52 break-all whitespace-normal text-xs text-muted-foreground">
                {route.account_id}
                <br />
                {route.deployment_id}
              </span>
            </TableCell>
            <TableCell className="align-top">
              <Badge variant="outline" className={healthPresentation[route.health].className}>
                {healthPresentation[route.health].label}
              </Badge>
              <span className="mt-1 block text-xs text-muted-foreground">{billingLabels[route.billing_mode]}</span>
            </TableCell>
            <TableCell className="align-top tabular-nums">
              {route.inflight} / {route.max_concurrency}
              <CapacityMeter value={route.inflight / route.max_concurrency} label="并发占用" tone="violet" />
              <CapacityMeter value={route.remaining_quota_ratio} label="余额可用" tone="emerald" />
            </TableCell>
            <TableCell className="align-top tabular-nums">
              {route.latency_ewma_ms === null ? "未知" : `${formatAccountPoolNumber(route.latency_ewma_ms)} ms`}
              <span className="mt-1 block max-w-48 whitespace-normal text-xs text-muted-foreground">
                {costText(route)}
              </span>
            </TableCell>
            <TableCell className="align-top">
              顺序 {route.manual_order ?? "自动"}
              <span className="mt-1 block text-xs text-muted-foreground">权重 {route.effective_weight}</span>
            </TableCell>
            <TableCell className="align-top">
              <Badge variant={route.available ? "secondary" : "destructive"}>
                {route.available ? "可调度" : reasonLabel(route.reason_code ?? route.unavailable_reason ?? "不可用")}
              </Badge>
              {route.retry_at && (
                <span className="mt-1 block text-xs text-muted-foreground">
                  恢复 {formatAccountPoolEpoch(route.retry_at, "")}
                </span>
              )}
            </TableCell>
            <TableCell className="text-right align-top">
              <Button
                variant="ghost"
                size="icon-sm"
                title="调整候选"
                disabled={!route.binding_id || !policy}
                onClick={() => onEdit(route)}
              >
                <Pencil />
                <span className="sr-only">调整候选</span>
              </Button>
            </TableCell>
          </TableRow>
        ))}
        {routes.length === 0 && (
          <TableRow>
            <TableCell colSpan={8} className="h-28 text-center text-muted-foreground">
              此模型暂无候选渠道
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}

function RoutingWorkspace({
  activeModel,
  activeSummary,
  routes,
  policy,
  loading,
  routeError,
  policyError,
  policyPending,
  onStrategyChange,
  onPreview,
  onEdit,
}: RoutingWorkspaceProps) {
  const strategy = policy?.strategy ?? activeSummary.strategy;
  return (
    <AccountPoolPanel
      title={activeModel}
      description={`版本 ${policy?.version ?? activeSummary.version}${routes.some((route) => route.dynamic_order) ? " · 动态策略预览" : ""}`}
      action={
        <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:flex-nowrap">
          <Button variant="outline" onClick={onPreview} disabled={routes.length === 0}>
            <Sparkles />
            识别并预览
          </Button>
          <Select
            value={strategy}
            onValueChange={(value) => value && onStrategyChange(value as RoutingStrategy)}
            disabled={!policy || policyPending}
          >
            <SelectTrigger className="w-full sm:w-48">
              <SelectValue>{strategyLabels[strategy]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(strategyLabels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      }
    >
      {policyError && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          正式策略管理不可用，当前仅显示运行路由
        </div>
      )}
      <RoutingTableContent routes={routes} policy={policy} loading={loading} routeError={routeError} onEdit={onEdit} />
    </AccountPoolPanel>
  );
}

export default function RoutingPanel({ accessToken }: RoutingPanelProps) {
  const queryClient = useQueryClient();
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [editingRoute, setEditingRoute] = useState<RoutingTableEntry | null>(null);
  const [autoPreviewOpen, setAutoPreviewOpen] = useState(false);
  const modelsQuery = useQuery({
    queryKey: accountPoolKeys.routingModels(),
    queryFn: () => getRoutingModels(accessToken),
  });
  const models = useMemo(() => modelsQuery.data ?? [], [modelsQuery.data]);
  const activeModel = useMemo(() => selectActiveModel(models, selectedModel), [models, selectedModel]);
  const policyQuery = useQuery({
    queryKey: accountPoolKeys.routingPolicy(activeModel ?? ""),
    queryFn: () => getRoutingPolicy(accessToken, activeModel!),
    enabled: Boolean(activeModel),
  });
  const routesQuery = useQuery({
    queryKey: accountPoolKeys.routingTable(activeModel ?? ""),
    queryFn: () => getRoutingTable(accessToken, activeModel!),
    enabled: Boolean(activeModel),
  });
  const refreshRouting = async () => {
    await queryClient.invalidateQueries({ queryKey: ["account-pool", "routing"] });
  };
  const policyMutation = useMutation({
    mutationFn: (strategy: RoutingStrategy) =>
      updateRoutingPolicy(accessToken, activeModel!, {
        expected_version: policyQuery.data!.version,
        strategy,
      }),
    onSuccess: async () => {
      NotificationsManager.success("调度策略已更新");
      await refreshRouting();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });
  const candidateMutation = useMutation({
    mutationFn: (request: { manualOrder: number | null; weight: number | null; paused: boolean }) => {
      const mutation: RoutingCandidateMutation = {
        expected_version: policyQuery.data!.version,
        manual_order: request.manualOrder,
        weight: request.weight,
        paused: request.paused,
      };
      return updateRoutingCandidate(accessToken, activeModel!, editingRoute!.binding_id!, mutation);
    },
    onSuccess: async () => {
      setEditingRoute(null);
      NotificationsManager.success("候选设置已更新");
      await refreshRouting();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });
  const resetMutation = useMutation({
    mutationFn: () =>
      resetRoutingCandidate(accessToken, activeModel!, editingRoute!.binding_id!, policyQuery.data!.version),
    onSuccess: async () => {
      setEditingRoute(null);
      NotificationsManager.success("候选已恢复自动设置");
      await refreshRouting();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  if (modelsQuery.isLoading || modelsQuery.isError || !activeModel) {
    return <RoutingPanelState loading={modelsQuery.isLoading} error={modelsQuery.isError} />;
  }

  const activeSummary = models.find((item) => item.model === activeModel)!;
  const routes = routesQuery.data ?? [];
  const policy = policyQuery.data;
  const loadingDetail = policyQuery.isLoading || routesQuery.isLoading;

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(240px,300px)_minmax(0,1fr)]">
      <AccountPoolPanel title="对外模型" description={`${models.length} 个模型路由表`}>
        <div className="divide-y">
          {models.map((model) => (
            <button
              key={model.model}
              type="button"
              className={`w-full px-4 py-3 text-left hover:bg-muted/50 ${model.model === activeModel ? "bg-muted" : ""}`}
              onClick={() => setSelectedModel(model.model)}
            >
              <strong className="block break-all text-sm">{model.model}</strong>
              <span className="mt-1 block text-xs text-muted-foreground">
                {strategyLabels[model.strategy]} · {model.available_accounts}/{model.accounts} 可用
              </span>
            </button>
          ))}
        </div>
      </AccountPoolPanel>

      <RoutingWorkspace
        activeModel={activeModel}
        activeSummary={activeSummary}
        routes={routes}
        policy={policy}
        loading={loadingDetail}
        routeError={routesQuery.isError}
        policyError={policyQuery.isError}
        policyPending={policyMutation.isPending}
        onStrategyChange={(strategy) => policyMutation.mutate(strategy)}
        onPreview={() => setAutoPreviewOpen(true)}
        onEdit={setEditingRoute}
      />

      {autoPreviewOpen && (
        <AutoRankingPreviewDialog routes={routes} model={activeModel} onClose={() => setAutoPreviewOpen(false)} />
      )}

      {editingRoute && policy && (
        <CandidateDialog
          key={`${editingRoute.binding_id}:${policy.version}`}
          route={editingRoute}
          policy={policy}
          pending={candidateMutation.isPending || resetMutation.isPending}
          onClose={() => setEditingRoute(null)}
          onSave={(manualOrder, weight, paused) => candidateMutation.mutate({ manualOrder, weight, paused })}
          onReset={() => resetMutation.mutate()}
        />
      )}
    </div>
  );
}
