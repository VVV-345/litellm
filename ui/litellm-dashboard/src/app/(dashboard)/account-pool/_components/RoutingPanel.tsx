// 本文件提供 LiteLLM Dashboard 的模型策略、路由顺序和候选覆盖管理界面。
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, RotateCcw, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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
import { buildAutoRankingPreview, type AutoRankingSignal } from "../autoRankingPreview";
import { reasonLabel } from "../reasonLabels";
import CapacityMeter from "./CapacityMeter";
import type {
  JsonDecimal,
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

const healthLabels: Record<RoutingTableEntry["health"], string> = {
  unknown: "等待请求",
  healthy: "正常",
  degraded: "异常",
  unhealthy: "不可用",
  half_open: "半开探测",
  cooldown: "冷却中",
  disabled: "已停用",
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

const autoRankingSignalLabels: Record<AutoRankingSignal, string> = {
  latency: "低延迟",
  quota: "高余额",
  cost: "低价格",
};

const healthBadgeClass: Record<RoutingTableEntry["health"], string> = {
  unknown: "border-slate-300 bg-slate-50 text-slate-700",
  healthy: "border-emerald-300 bg-emerald-50 text-emerald-800",
  degraded: "border-amber-300 bg-amber-50 text-amber-900",
  unhealthy: "border-red-300 bg-red-50 text-red-800",
  half_open: "border-sky-300 bg-sky-50 text-sky-800",
  cooldown: "border-orange-300 bg-orange-50 text-orange-900",
  disabled: "border-slate-300 bg-slate-100 text-slate-700",
};

const formatNumber = (value: JsonDecimal | null, maximumFractionDigits = 2): string => {
  if (value === null) return "未知";
  const numericValue = Number(value);
  return Number.isFinite(numericValue)
    ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(numericValue)
    : "未知";
};

const formatRecovery = (value: number | null): string =>
  value === null ? "" : new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });

const optionalInteger = (value: string): number | null => (value.trim() === "" ? null : Number(value));

const selectActiveModel = (models: RoutingModelSummary[], selectedModel: string | null): string | null => {
  const selected = selectedModel === null ? undefined : models.find((item) => item.model === selectedModel);
  return selected?.model ?? models[0]?.model ?? null;
};

const costText = (route: RoutingTableEntry): string => {
  const evidence = route.cost_evidence;
  if (!evidence) return "未知";
  if (evidence.kind === "subscription_included") return "套餐内包含";
  return `${formatNumber(evidence.effective_cost)} ${evidence.currency}/${evidence.unit}${evidence.partial ? "（部分）" : ""}`;
};

const sortReasonText = (route: RoutingTableEntry): string =>
  route.sort_reason_codes.map((code) => sortReasonLabels[code] ?? code).join(" / ") || "稳定顺序";

function AutoRankingPreviewDialog({
  routes,
  model,
  onClose,
}: {
  routes: RoutingTableEntry[];
  model: string;
  onClose: () => void;
}) {
  const preview = useMemo(() => buildAutoRankingPreview(routes), [routes]);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[min(720px,calc(100vh-2rem))] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>自动排序建议</DialogTitle>
          <DialogDescription>
            {model} 的建议基于当前健康状态、延迟、余额和可比较价格生成，不会修改正式调度策略或人工顺序
          </DialogDescription>
        </DialogHeader>
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>建议</TableHead>
                <TableHead>渠道</TableHead>
                <TableHead>识别依据</TableHead>
                <TableHead className="text-right">评分</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {preview.map((entry) => (
                <TableRow
                  key={`${entry.route.account_id}:${entry.route.deployment_id}:${entry.route.billing_route_id ?? ""}`}
                >
                  <TableCell className="font-medium tabular-nums">{entry.position}</TableCell>
                  <TableCell>
                    <span className="block font-medium">{entry.route.display_name}</span>
                    <span className="mt-1 block text-xs text-muted-foreground">{entry.route.deployment_id}</span>
                  </TableCell>
                  <TableCell>
                    {entry.route.available ? (
                      entry.signals.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {entry.signals.map((signal) => (
                            <Badge key={signal} variant="outline" className="border-sky-200 bg-sky-50 text-sky-800">
                              {autoRankingSignalLabels[signal]}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">尚无可用的延迟、余额或价格数据</span>
                      )
                    ) : (
                      <Badge variant="outline" className="border-red-300 bg-red-50 text-red-800">
                        当前不可调度
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {entry.score === null ? "-" : formatNumber(entry.score, 0)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface CandidateDialogProps {
  route: RoutingTableEntry;
  policy: RoutingPolicyState;
  pending: boolean;
  onClose: () => void;
  onSave: (manualOrder: number | null, weight: number | null, paused: boolean) => void;
  onReset: () => void;
}

function CandidateDialog({ route, policy, pending, onClose, onSave, onReset }: CandidateDialogProps) {
  const existing = policy.overrides.find((override) => override.binding_id === route.binding_id);
  const [manualOrder, setManualOrder] = useState(route.manual_order?.toString() ?? "");
  const [weight, setWeight] = useState(existing?.weight?.toString() ?? "");
  const [paused, setPaused] = useState(route.routing_paused);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{route.display_name}</DialogTitle>
          <DialogDescription className="break-all">
            {route.account_id} · {route.deployment_id}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="routing-manual-order">人工顺序</Label>
            <Input
              id="routing-manual-order"
              type="number"
              min={0}
              placeholder="自动"
              value={manualOrder}
              onChange={(event) => setManualOrder(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="routing-weight">模型权重</Label>
            <Input
              id="routing-weight"
              type="number"
              min={1}
              max={100}
              placeholder="继承渠道"
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
            />
          </div>
          <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2.5">
            <Label htmlFor="routing-paused">暂停此模型绑定</Label>
            <Switch id="routing-paused" checked={paused} onCheckedChange={setPaused} />
          </div>
        </div>
        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" onClick={onReset} disabled={pending || !existing}>
            <RotateCcw />
            恢复自动
          </Button>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} disabled={pending}>
              取消
            </Button>
            <Button
              onClick={() => onSave(optionalInteger(manualOrder), optionalInteger(weight), paused)}
              disabled={pending}
            >
              {pending && <Loader2 className="animate-spin" />}
              保存
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface RoutingPanelProps {
  accessToken: string;
}

function RoutingPanelState({ loading, error }: { loading: boolean; error: boolean }) {
  if (loading) {
    return (
      <div className="flex min-h-80 items-center justify-center">
        <Loader2 className="animate-spin" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-md border border-destructive/30 px-4 py-10 text-center text-sm text-destructive">
        无法读取模型调度表
      </div>
    );
  }
  return <div className="rounded-md border px-4 py-16 text-center text-sm text-muted-foreground">尚未配置模型绑定</div>;
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
    return (
      <div className="flex min-h-72 items-center justify-center">
        <Loader2 className="animate-spin" />
      </div>
    );
  }
  if (routeError) {
    return (
      <div className="px-4 py-10 text-center text-sm text-destructive">
        无法读取此模型的运行路由，请检查 Account Pool 调度服务
      </div>
    );
  }
  return (
    <Table>
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
              <Badge variant="outline" className={healthBadgeClass[route.health]}>
                {healthLabels[route.health]}
              </Badge>
              <span className="mt-1 block text-xs text-muted-foreground">{billingLabels[route.billing_mode]}</span>
            </TableCell>
            <TableCell className="align-top tabular-nums">
              {route.inflight} / {route.max_concurrency}
              <CapacityMeter value={route.inflight / route.max_concurrency} label="并发占用" tone="violet" />
              <CapacityMeter value={route.remaining_quota_ratio} label="余额可用" tone="emerald" />
            </TableCell>
            <TableCell className="align-top tabular-nums">
              {route.latency_ewma_ms === null ? "未知" : `${formatNumber(route.latency_ewma_ms)} ms`}
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
                <span className="mt-1 block text-xs text-muted-foreground">恢复 {formatRecovery(route.retry_at)}</span>
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
    <section className="min-w-0 overflow-hidden rounded-md border bg-background">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="break-all text-base font-semibold">{activeModel}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            版本 {policy?.version ?? activeSummary.version}
            {routes.some((route) => route.dynamic_order) ? " · 动态策略预览" : ""}
          </p>
        </div>
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
      </div>
      {policyError && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          正式策略管理不可用，当前仅显示运行路由
        </div>
      )}
      <RoutingTableContent routes={routes} policy={policy} loading={loading} routeError={routeError} onEdit={onEdit} />
    </section>
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
      <section className="min-w-0 overflow-hidden rounded-md border bg-background">
        <div className="border-b px-4 py-3">
          <h2 className="text-sm font-semibold">对外模型</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{models.length} 个模型路由表</p>
        </div>
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
      </section>

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
