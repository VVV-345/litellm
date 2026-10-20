"use client";

import { AdminOnlyNotice } from "@/components/shared/AdminOnlyNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getCallbacksCall, setCallbacksCall } from "@/components/networking";
import { RuntimeConfigDialog } from "@/components/Settings/RuntimeSettings/RuntimeConfigDialog";
import RuntimeSettings from "@/components/Settings/RuntimeSettings/RuntimeSettings";
import { RuntimePolicyDialog } from "@/components/Settings/RuntimeSettings/RuntimePolicyDialog";
import type { RuntimeSettingsCategory } from "@/components/Settings/RuntimeSettings/RuntimeSettingsSection";
import { AccountPoolRoutingFields, accountPoolRoutingValue } from "@/components/router_settings/AccountPoolRoutingFields";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import {
  getAccountPoolRoutingStatus,
  getAccountPoolSettings,
  resetAccountPolicy,
  updateAccountPoolSettings,
  type AccountPoolRoutingStatus,
  type AccountPoolSettingsView,
  type PolicyView,
} from "@/features/account-pool/api/AccountPoolManagementApi";
import { accountPoolPolicyOptions } from "@/features/account-pool/hooks/accountPoolOptions";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";
import { useAccountPoolQuery } from "@/features/account-pool/hooks/useAccountPoolQuery";
import { canManageAccountPool } from "@/features/account-pool/utils/AccountPoolPermissions";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import { toast } from "@/lib/toast";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

const reasonLabels: Record<string, string> = {
  model_cooldown: "模型冷却中",
  subscription_expired: "订阅已过期",
  quota_exhausted: "额度已触线",
  quota_snapshot_missing: "缺少额度快照",
  quota_snapshot_expired: "额度快照过期",
};

const gateLabels: Record<string, string> = {
  model_cooldown: "模型冷却",
  subscription: "订阅有效期",
  quota: "额度窗口",
  freshness: "快照新鲜度",
};

type DefaultsDraft = {
  quota_reserve_percent: string;
  quota_reserve_5h_percent: string;
  quota_reserve_weekly_percent: string;
  quota_snapshot_max_age: string;
};

const draftFrom = (view: AccountPoolSettingsView): DefaultsDraft => ({
  quota_reserve_percent: String(view.values.quota_reserve_percent ?? 0),
  quota_reserve_5h_percent:
    view.values.quota_reserve_5h_percent === null || view.values.quota_reserve_5h_percent === undefined
      ? ""
      : String(view.values.quota_reserve_5h_percent),
  quota_reserve_weekly_percent:
    view.values.quota_reserve_weekly_percent === null || view.values.quota_reserve_weekly_percent === undefined
      ? ""
      : String(view.values.quota_reserve_weekly_percent),
  quota_snapshot_max_age: String(view.values.quota_snapshot_max_age ?? 300),
});

const integer = (value: string, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const optionalInteger = (value: string): number | null => (value.trim() === "" ? null : integer(value, 0));

type SettingsCategory = { value: RuntimeSettingsCategory; label: string };

const routingCategories: readonly SettingsCategory[] = [
  { value: "common", label: "新卡片默认值" },
  { value: "access", label: "模型访问" },
  { value: "network", label: "网络与重试" },
  { value: "quota", label: "配额回退" },
];

const runtimeCategories: readonly SettingsCategory[] = [
  { value: "streaming", label: "流式传输" },
  { value: "advanced", label: "高级运行" },
  { value: "payload", label: "请求改写" },
];

function RuntimeSettingsGroup({
  id,
  categories,
}: {
  id: string;
  categories: readonly SettingsCategory[];
}) {
  const [category, setCategory] = useState<RuntimeSettingsCategory>(categories[0].value);
  const selectCategory = (value: string | null) => {
    const next = categories.find((item) => item.value === value);
    if (next) setCategory(next.value);
  };

  return (
    <div className="grid gap-3">
      <div className="space-y-2">
        <Label htmlFor={id}>设置分组</Label>
        <Select value={category} onValueChange={selectCategory}>
          <SelectTrigger id={id} className="w-full md:w-80">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {categories.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <RuntimeSettings category={category} />
    </div>
  );
}

function PolicyDefaultsForm({
  view,
  accessToken,
  onSaved,
}: {
  view: AccountPoolSettingsView;
  accessToken: string;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<DefaultsDraft>(() => draftFrom(view));
  const [saving, setSaving] = useState(false);
  const update = (field: keyof DefaultsDraft, value: string) => setDraft((current) => ({ ...current, [field]: value }));

  const save = async () => {
    setSaving(true);
    try {
      await updateAccountPoolSettings(
        accessToken,
        {
          version: view.version,
          values: {
            ...view.values,
            quota_reserve_percent: integer(draft.quota_reserve_percent, 0),
            quota_reserve_5h_percent: optionalInteger(draft.quota_reserve_5h_percent),
            quota_reserve_weekly_percent: optionalInteger(draft.quota_reserve_weekly_percent),
            quota_snapshot_max_age: integer(draft.quota_snapshot_max_age, 300),
          },
        },
        "policy",
      );
      await onSaved();
      toast.success("卡内默认策略已保存");
    } catch {
      toast.error("保存失败，请刷新后重试");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="quota-reserve">额度预留（%）</Label>
          <Input
            id="quota-reserve"
            type="number"
            min={0}
            max={100}
            value={draft.quota_reserve_percent}
            onChange={(event) => update("quota_reserve_percent", event.target.value)}
          />
          <p className="text-xs text-muted-foreground">任一未过期窗口的剩余额度达到或低于该值时停止分配</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="snapshot-max-age">快照有效期（秒）</Label>
          <Input
            id="snapshot-max-age"
            type="number"
            min={10}
            value={draft.quota_snapshot_max_age}
            onChange={(event) => update("quota_snapshot_max_age", event.target.value)}
          />
          <p className="text-xs text-muted-foreground">不得低于两个额度刷新周期；过期后在宽限期内降级可用</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="quota-reserve-5h">5 小时窗口预留（%）</Label>
          <Input
            id="quota-reserve-5h"
            type="number"
            min={0}
            max={100}
            placeholder="留空沿用通用比例"
            value={draft.quota_reserve_5h_percent}
            onChange={(event) => update("quota_reserve_5h_percent", event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="quota-reserve-weekly">周窗口预留（%）</Label>
          <Input
            id="quota-reserve-weekly"
            type="number"
            min={0}
            max={100}
            placeholder="留空沿用通用比例"
            value={draft.quota_reserve_weekly_percent}
            onChange={(event) => update("quota_reserve_weekly_percent", event.target.value)}
          />
        </div>
      </div>
      <div>
        <Button onClick={save} disabled={saving}>
          {saving ? "保存中" : "保存卡内默认"}
        </Button>
      </div>
    </div>
  );
}

function RoutingStatusPanel({
  cards,
  accessToken,
}: {
  cards: { id: string; name: string; enabled_models: string[] }[];
  accessToken: string;
}) {
  const [cardId, setCardId] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<AccountPoolRoutingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const selected = cards.find((card) => card.id === cardId);
  const models = selected?.enabled_models ?? [];

  const selectCard = (value: string | null) => {
    if (!value) return;
    setCardId(value);
    setStatus(null);
    setModel(cards.find((card) => card.id === value)?.enabled_models[0] ?? "");
  };

  const inspect = async () => {
    if (!cardId || !model) {
      setError("请选择卡片并填写模型");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setStatus(await getAccountPoolRoutingStatus(accessToken, cardId, model));
    } catch {
      setStatus(null);
      setError("该卡片还没有可用的路由快照，等待一次对账后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>路由判定</CardTitle>
        <CardDescription>查看某张卡片在指定模型下是否可用，以及被哪道门拦下</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
          <Select value={cardId} onValueChange={selectCard}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="选择卡片" />
            </SelectTrigger>
            <SelectContent>
              {cards.map((card) => (
                <SelectItem key={card.id} value={card.id}>
                  {card.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            list="routing-status-models"
            placeholder="模型"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <datalist id="routing-status-models">
            {models.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <Button onClick={inspect} disabled={loading}>
            {loading ? "检查中" : "查看判定"}
          </Button>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {status && (
          <div className="grid gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={status.eligible ? "default" : "destructive"}>
                {status.eligible ? "可用" : reasonLabels[status.reason ?? ""] ?? status.reason}
              </Badge>
              <span className="text-muted-foreground">
                模型 {status.resolved_model}，观测年龄 {status.age_seconds == null ? "未知" : `${Math.round(status.age_seconds)} 秒`}
              </span>
              <span className="text-muted-foreground">
                策略来源：{status.inherits_global ? "继承全局" : `独立配置 v${status.policy_version}`}
              </span>
              <span className="text-muted-foreground">
                有效期 {status.max_age_seconds} 秒，宽限至 {status.stale_grace_seconds} 秒
              </span>
            </div>
            {status.windows.length > 0 && (
              <div className="grid gap-2">
                {status.windows.map((window, index) => (
                  <div
                    key={`${window.window_minutes ?? "unknown"}-${index}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded border px-3 py-2"
                  >
                    <span>{window.window_minutes ? `${window.window_minutes} 分钟窗口` : "通用窗口"}</span>
                    <span className={window.tripped ? "text-destructive" : "text-muted-foreground"}>
                      剩余 {window.remaining_percent.toFixed(1)}%，阈值 {window.threshold_percent}%，
                      {window.tripped ? "已触线" : "未触线"}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="grid gap-1 text-muted-foreground">
              {status.gates.map((gate) => (
                <span key={gate.gate}>
                  {gateLabels[gate.gate] ?? gate.gate}：{gate.passed ? "通过" : "拦截"}
                  {gate.detail ? `（${gate.detail}）` : ""}
                </span>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function PolicyCenterPage() {
  const { accessToken, userId, userRole, isViewOnly } = useAuthorized();
  const canManage = canManageAccountPool(userRole, isViewOnly);
  const queryClient = useQueryClient();
  const [routing, setRouting] = useState<ReturnType<typeof accountPoolRoutingValue> | null>(null);
  const [policyCard, setPolicyCard] = useState<AccountPoolEnvironment | null>(null);
  const [runtimeCard, setRuntimeCard] = useState<AccountPoolEnvironment | null>(null);
  const environments = useAccountPoolQuery(accessToken, canManage, false);
  const policies = useQuery({ ...accountPoolPolicyOptions(accessToken), enabled: Boolean(accessToken && canManage) });
  const hasIdentity = Boolean(accessToken && userId && userRole);
  const routerConfigEnabled = hasIdentity && canManage;
  const settingsQuery = {
    queryKey: [...accountPoolQueryKeys.settings(accessToken), "policy-center"],
    queryFn: () => getAccountPoolSettings(accessToken as string),
    enabled: Boolean(accessToken && canManage),
    retry: false,
  };
  const routerConfigQuery = {
    queryKey: ["router-config", accessToken],
    queryFn: () => getCallbacksCall(accessToken as string, userId as string, userRole as string),
    enabled: routerConfigEnabled,
    retry: false,
  };
  const settings = useQuery(settingsQuery);
  const routerConfig = useQuery(routerConfigQuery);

  const cards = useMemo(() => environments.data ?? [], [environments.data]);
  const policyByCard = useMemo(() => {
    const map = new Map<string, PolicyView>();
    for (const policy of policies.data ?? []) map.set(policy.card_id, policy);
    return map;
  }, [policies.data]);
  const independent = cards.filter((card) => (policyByCard.get(card.id)?.version ?? 0) > 0);
  const inherited = cards.length - independent.length;
  const currentRouting = routing ?? accountPoolRoutingValue(routerConfig.data?.router_settings?.account_pool_routing);
  const routingCards = useMemo(
    () => cards.map((card) => ({ id: card.id, name: card.name, enabled_models: card.enabled_models })),
    [cards],
  );

  const saveRouting = async () => {
    if (!accessToken || !routerConfig.data) return;
    try {
      await setCallbacksCall(accessToken, {
        router_settings: {
          ...(routerConfig.data.router_settings ?? {}),
          account_pool_routing: currentRouting,
        },
      });
      toast.success("跨卡调度已保存");
      await queryClient.invalidateQueries({ queryKey: ["router-config", accessToken] });
    } catch {
      toast.error("保存失败，请刷新后重试");
    }
  };

  const resetPolicy = async (cardId: string, name: string) => {
    if (!accessToken || !window.confirm(`将「${name}」回归全局默认？这会删除这张卡片的独立策略。`)) return;
    try {
      await resetAccountPolicy(accessToken, cardId);
      toast.success("已回归全局默认");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.policies(accessToken) }),
        queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot }),
        queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.settings(accessToken) }),
      ]);
    } catch {
      toast.error("回归失败，运行时同步未完成，策略保持不变");
    }
  };

  if (!canManage) return <AdminOnlyNotice pageTitle="策略中心" />;
  if (!accessToken) return null;

  return (
    <div className="grid gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">策略中心</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          跨卡调度写 LiteLLM 路由配置，卡内默认写号池全局设置，卡片独立配置只覆盖自己
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>职责对照</CardTitle>
          <CardDescription>同一个字段只在一个区块出现</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 text-sm md:grid-cols-3">
          <div>
            <p className="font-medium">跨卡调度</p>
            <p className="mt-1 text-muted-foreground">选卡策略、指定卡片顺序、会话粘卡与有效期</p>
          </div>
          <div>
            <p className="font-medium">卡内默认</p>
            <p className="mt-1 text-muted-foreground">卡内路由、重试、超时、并发、额度预留与快照有效期</p>
          </div>
          <div>
            <p className="font-medium">卡片独立</p>
            <p className="mt-1 text-muted-foreground">只有显式设为独立的卡片才覆盖默认值，可随时回归</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>跨卡调度</CardTitle>
          <CardDescription>写入 LiteLLM Router 的 account_pool_routing，对所有卡片生效；保存会回写整份 Router 设置，多标签同时编辑请先刷新</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {routerConfig.isLoading ? (
            <p className="text-sm text-muted-foreground">加载中</p>
          ) : (
            <AccountPoolRoutingFields value={currentRouting} onChange={setRouting} />
          )}
          <div>
            <Button onClick={saveRouting} disabled={!routerConfig.data}>
              保存跨卡调度
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>卡内默认</CardTitle>
          <CardDescription>
            当前 {inherited} 张卡片继承全局，{independent.length} 张卡片独立配置；额度阈值只自动作用于继承卡，分组档只能覆盖回退开关
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6">
          {settings.data ? (
            <PolicyDefaultsForm
              key={settings.data.version}
              view={settings.data}
              accessToken={accessToken}
              onSaved={() =>
                queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.settings(accessToken) })
              }
            />
          ) : (
            <p className="text-sm text-muted-foreground">加载中</p>
          )}
          <RuntimeSettingsGroup id="policy-routing-category" categories={routingCategories} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>运行参数</CardTitle>
          <CardDescription>流式、插件与请求改写，保存后由号池运行时同步</CardDescription>
        </CardHeader>
        <CardContent>
          <RuntimeSettingsGroup id="policy-runtime-category" categories={runtimeCategories} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>卡片独立配置</CardTitle>
          <CardDescription>独立配置的卡片不跟随上面的默认值，包括额度预留与快照有效期</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          {cards.map((card) => {
            const policy = policyByCard.get(card.id);
            const independentCard = (policy?.version ?? 0) > 0;
            return (
              <div
                key={card.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded border px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{card.name}</span>
                  <Badge variant={independentCard ? "secondary" : "outline"}>
                    {independentCard ? `独立配置 v${policy?.version}` : "继承全局"}
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => setPolicyCard(card)}>
                    {independentCard ? "编辑独立配置" : "设为独立配置"}
                  </Button>
                  {independentCard && (
                    <Button variant="outline" onClick={() => resetPolicy(card.id, card.name)}>
                      回归全局
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      <RoutingStatusPanel cards={routingCards} accessToken={accessToken} />

      {policyCard && (
        <RuntimePolicyDialog
          accessToken={accessToken}
          environment={policyCard}
          environments={cards}
          policies={policies.data ?? []}
          onOpenRuntimeConfig={() => {
            setRuntimeCard(policyCard);
            setPolicyCard(null);
          }}
          onClose={() => setPolicyCard(null)}
        />
      )}
      {runtimeCard && (
        <RuntimeConfigDialog
          accessToken={accessToken}
          environment={runtimeCard}
          open
          onOpenChange={(open) => !open && setRuntimeCard(null)}
          onRefresh={async () => {
            await queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot });
          }}
          onSaved={async () => {
            setRuntimeCard(null);
            await queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot });
          }}
        />
      )}
    </div>
  );
}
