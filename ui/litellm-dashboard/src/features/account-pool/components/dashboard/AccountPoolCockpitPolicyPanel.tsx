/** 读取并显式保存 Cockpit 全局号池策略，保留版本冲突和待确认状态。 */
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  getAccountPoolCockpitPolicy,
  getAccountPoolCockpitPolicyRuntime,
  applyAccountPoolCockpitPolicy,
  saveAccountPoolCockpitPolicy,
  type CockpitPolicyApply,
  type CockpitPolicyRuntime,
  type CockpitPolicyUpdate,
  type CockpitPolicyView,
} from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { cockpitPolicyDraft, cockpitPolicyUpdate, type CockpitPolicyDraft } from "../../utils/cockpitPolicyDraft";

const strategies: Record<string, string> = {
  auto: "自动",
  random: "随机",
  quota_high_first: "剩余额度高优先",
  quota_low_first: "剩余额度低优先",
  plan_high_first: "套餐等级高优先",
  plan_low_first: "套餐等级低优先",
  expiry_soon_first: "到期时间近优先",
  custom: "自定义优先级与权重",
};

const blockedReasons: Record<NonNullable<CockpitPolicyView["blocked_reason"]>, string> = {
  unsupported_strategy: "当前保存的选择策略不受支持，已禁用编辑",
  unsupported_account_or_rule: "账号类型、已有规则或账号数量不受支持，已禁用编辑",
};

export function AccountPoolCockpitPolicyPanel({
  accessToken,
  native = false,
}: {
  accessToken: string;
  native?: boolean;
}) {
  return <CockpitPolicyEditor key={accessToken + String(native)} accessToken={accessToken} native={native} />;
}

function CockpitPolicyEditor({ accessToken, native }: { accessToken: string; native: boolean }) {
  const client = useQueryClient();
  const queryKey = native
    ? accountPoolQueryKeys.nativeCockpitPolicy(accessToken)
    : accountPoolQueryKeys.cockpitPolicy(accessToken);
  const queryOptions = {
    queryKey,
    queryFn: () => getAccountPoolCockpitPolicy(accessToken, native),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    staleTime: 0,
  };
  const query = useQuery(queryOptions);
  const [draft, setDraft] = useState<CockpitPolicyDraft | null>(null);
  const [runtime, setRuntime] = useState<CockpitPolicyRuntime | null>(null);
  const apply = useMutation({
    mutationFn: (body: CockpitPolicyApply) => {
      setRuntime(null);
      return applyAccountPoolCockpitPolicy(accessToken, body, native);
    },
    retry: false,
    onSuccess: (result) => setRuntime(result),
  });
  const save = useMutation({
    mutationFn: (body: CockpitPolicyUpdate) => {
      setRuntime(null);
      return saveAccountPoolCockpitPolicy(accessToken, body, native);
    },
    retry: false,
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
    },
  });
  const reload = useMutation({
    mutationFn: () => {
      setRuntime(null);
      return getAccountPoolCockpitPolicy(accessToken, native);
    },
    retry: false,
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
      save.reset();
    },
  });
  const checkRuntime = useMutation({
    mutationFn: async () => {
      setRuntime(null);
      const policy = await getAccountPoolCockpitPolicy(accessToken, native);
      const observation = await getAccountPoolCockpitPolicyRuntime(accessToken, native);
      if (observation.persisted_revision !== policy.revision) {
        throw new Error("保存版本与运行状态不一致，请重新检查");
      }
      return { policy, observation };
    },
    retry: false,
    onSuccess: ({ policy, observation }) => {
      client.setQueryData(queryKey, policy);
      setRuntime(observation);
      save.reset();
      apply.reset();
      reload.reset();
    },
  });
  const data = query.data;
  const form = draft ?? (data ? cockpitPolicyDraft(data) : null);
  const busy = [query.isFetching, save.isPending, reload.isPending, apply.isPending, checkRuntime.isPending].some(
    Boolean,
  );
  const failed = [query.isError, save.isError, reload.isError, apply.isError].some(Boolean);
  const disabled = busy || !data?.editable || failed;
  const applyDisabled = [
    disabled,
    draft !== null,
    !runtime?.apply_supported,
    runtime?.persisted_revision !== data?.revision,
    runtime?.active_revision === data?.revision,
  ].some(Boolean);
  const payload = form && data ? cockpitPolicyUpdate(form, data.supported_strategies) : null;
  const edit = (changes: Partial<CockpitPolicyDraft>) => {
    if (!form || disabled) return;
    setDraft({ ...form, ...changes });
    setRuntime(null);
    save.reset();
  };
  const error = reload.error ?? query.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cockpit Codex 号池全局策略</CardTitle>
        <CardDescription>
          作用于整个 Codex 号池，不是单卡、密钥或团队策略，不切换现有 LiteLLM
          流量。保存只写入配置，不会重启服务，也不确认运行中已经采用此策略。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-amber-700 dark:text-amber-400">
          仅显式应用已保存的 Codex 全局排序策略，不重启服务，不迁移流量、凭据或其他配置
        </p>
        <p className="text-xs text-muted-foreground">
          当前仅支持 OAuth 账号子集，最多 256 个账号。此处不修改账号启用、候选范围、回退、最多尝试次数、冷却或会话亲和性
        </p>
        {query.isPending && <p role="status">正在读取全局策略…</p>}
        <CockpitPolicyError readError={error} saveError={save.error} />
        {data && <p className="break-all text-xs">已保存版本：{data.revision}</p>}
        <CockpitRuntimeStatus runtime={runtime} revision={data?.revision} />
        {apply.error && <p role="alert">应用未确认，必须重新检查运行状态并核对保存版本：{apply.error.message}</p>}
        {checkRuntime.error && <p role="alert">运行状态检查失败：{checkRuntime.error.message}</p>}
        {data && form && (
          <CockpitPolicyFields data={data} form={form} disabled={disabled} valid={payload !== null} onChange={edit} />
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={disabled || !draft || !payload}
            onClick={() => {
              if (!disabled && draft && payload) save.mutate(payload);
            }}
          >
            {save.isPending ? "正在保存策略" : "保存全局策略"}
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => reload.mutate()}>
            重新读取策略
          </Button>
          <Button variant="outline" disabled={busy || draft !== null} onClick={() => checkRuntime.mutate()}>
            {checkRuntime.isPending ? "正在检查运行状态" : "检查运行状态"}
          </Button>
          <Button
            disabled={applyDisabled}
            onClick={() => {
              if (!applyDisabled && data) apply.mutate({ revision: data.revision });
            }}
          >
            {apply.isPending ? "正在应用策略" : "应用已保存策略"}
          </Button>
        </div>
        {save.isSuccess && <p role="status">已保存，请检查运行状态后显式应用</p>}
      </CardContent>
    </Card>
  );
}

function CockpitRuntimeStatus({ runtime, revision }: { runtime: CockpitPolicyRuntime | null; revision?: string }) {
  if (!runtime || runtime.persisted_revision !== revision) return <p>运行状态尚未确认，请检查运行状态</p>;
  const reasons = {
    non_policy_change: "包含非策略配置变更，不能单独应用",
    unsupported_policy: "当前策略不支持在线应用",
    runtime_unavailable: "运行状态不可用，不能应用",
  };
  if (runtime.blocked_reason) return <p>{reasons[runtime.blocked_reason]}</p>;
  if (runtime.active_revision === revision) {
    return <p className="break-all">已确认 Codex 全局排序运行版本：{runtime.active_revision}</p>;
  }
  return <p className="break-all">运行版本：{runtime.active_revision}，尚未采用当前保存策略</p>;
}

function CockpitPolicyFields({
  data,
  form,
  disabled,
  valid,
  onChange,
}: {
  data: CockpitPolicyView;
  form: CockpitPolicyDraft;
  disabled: boolean;
  valid: boolean;
  onChange: (changes: Partial<CockpitPolicyDraft>) => void;
}) {
  const editRule = (index: number, changes: Partial<CockpitPolicyDraft["custom_rules"][number]>) => {
    onChange({
      custom_rules: form.custom_rules.map((rule, position) => (position === index ? { ...rule, ...changes } : rule)),
    });
  };
  return (
    <>
      {!data.editable && (
        <p>{data.blocked_reason ? blockedReasons[data.blocked_reason] : "当前号池没有可编辑的账号"}</p>
      )}
      <div className="space-y-2">
        <Label htmlFor="cockpit-strategy">选择策略</Label>
        <Select
          value={form.routing_strategy}
          onValueChange={(value) => {
            if (value) onChange({ routing_strategy: value });
          }}
          disabled={disabled}
        >
          <SelectTrigger id="cockpit-strategy" className="w-full">
            <SelectValue>{strategies[form.routing_strategy] ?? form.routing_strategy}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {data.supported_strategies.map((strategy) => (
              <SelectItem key={strategy} value={strategy}>
                {strategies[strategy]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <p className="text-xs text-muted-foreground">
        自定义策略按优先级从高到低选择，同优先级按权重（1 至 10000）选择。优先使用和备用账号互斥，适用于所有策略。
      </p>
      <div className="space-y-3">
        {form.custom_rules.map((rule, index) => (
          <fieldset key={rule.account_id} className="rounded-lg border p-3">
            <legend className="max-w-full break-all px-1 text-sm">{rule.account_id}</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm">
                <span>优先级</span>
                <Input
                  aria-label={`${rule.account_id} 优先级`}
                  min="-10000"
                  max="10000"
                  type="number"
                  step="1"
                  value={rule.priority}
                  disabled={disabled}
                  onChange={(event) => editRule(index, { priority: event.target.value })}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span>权重</span>
                <Input
                  aria-label={`${rule.account_id} 权重`}
                  type="number"
                  min="1"
                  max="10000"
                  step="1"
                  value={rule.weight}
                  disabled={disabled}
                  onChange={(event) => editRule(index, { weight: event.target.value })}
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={rule.is_preferred}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    editRule(index, { is_preferred: checked, ...(checked ? { is_backup: false } : {}) })
                  }
                />
                <span>
                  <span className="sr-only">{rule.account_id}</span> 优先使用
                </span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={rule.is_backup}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    editRule(index, { is_backup: checked, ...(checked ? { is_preferred: false } : {}) })
                  }
                />
                <span>
                  <span className="sr-only">{rule.account_id}</span> 备用账号
                </span>
              </label>
            </div>
          </fieldset>
        ))}
      </div>
      {!valid && data.editable && (
        <p role="alert">
          请选择支持的策略，优先级须为 -10000 至 10000 的整数，权重须为 1 至 10000
          的整数，优先使用与备用账号不能同时选中
        </p>
      )}
    </>
  );
}

function CockpitPolicyError({ readError, saveError }: { readError: Error | null; saveError: Error | null }) {
  if (!readError && !saveError) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {saveError && `保存未确认，必须重新读取策略后才能再次编辑或保存：${saveError.message}。`}
      {readError && `读取失败：${readError.message}`}
    </p>
  );
}
