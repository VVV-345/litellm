/** 本文件编辑已授权号池环境的运行配置，并将表单变更一次性提交给管理 API。 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { ApiError } from "@/lib/http/client";
import { toast } from "@/lib/toast";

import {
  getAccountPoolEnvironment,
  listAccountPoolProxyProfiles,
  updateAccountPoolEnvironment,
} from "./AccountPoolApi";
import {
  formatDateTime,
  formatQuota,
  canConfigureEnvironment,
  mostConstrainedWindow,
  quotaRows,
  validateProxyProfileSelection,
  validateAccountPoolUpdate,
} from "./AccountPoolFormatters";
import { toUpdateRequest } from "./AccountPoolTypes";
import type { AccountPoolEnvironment, AccountPoolUpdateRequest } from "./AccountPoolTypes";

interface AccountPoolConfigDialogProps {
  accessToken: string | null;
  environment: AccountPoolEnvironment;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void;
  onSaved: (environment: AccountPoolEnvironment) => void;
}

export const AccountPoolConfigDialog = ({
  accessToken,
  environment,
  open,
  onOpenChange,
  onRefresh,
  onSaved,
}: AccountPoolConfigDialogProps) => {
  const [form, setForm] = useState<AccountPoolUpdateRequest>(() => toUpdateRequest(environment));
  const [saving, setSaving] = useState(false);
  const lifecycleDisabled = !canConfigureEnvironment(environment);
  const profilesQuery = useQuery({
    queryKey: ["account-pool", "proxy-profiles", accessToken],
    queryFn: () => {
      if (!accessToken) throw new Error("Access token required");
      return listAccountPoolProxyProfiles(accessToken);
    },
    enabled: open && accessToken !== null,
    retry: false,
  });
  const profiles = profilesQuery.data ?? [];
  const profilesLoading = profilesQuery.isLoading || profilesQuery.isFetching;
  const profilesError = profilesQuery.isError ? "代理 Profile 列表加载失败，请稍后重试" : null;
  const profileSelectionError =
    form.proxy_mode === "profile" && !profilesError
      ? validateProxyProfileSelection(form.proxy_mode, form.proxy_profile_id, profiles)
      : null;

  const quotaWindow = useMemo(() => mostConstrainedWindow(environment), [environment]);
  const rows = useMemo(() => quotaRows(environment), [environment]);
  const update = <K extends keyof AccountPoolUpdateRequest>(key: K, value: AccountPoolUpdateRequest[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const handleProxyModeChange = (value: string | null) => {
    if (value === "default_gateway") {
      setForm((current) => ({ ...current, proxy_mode: "default_gateway", proxy_profile_id: null }));
    } else if (value === "profile") {
      setForm((current) => ({ ...current, proxy_mode: "profile" }));
    }
  };

  const handleSave = async () => {
    if (!accessToken || lifecycleDisabled) return;
    const validationError = validateAccountPoolUpdate(form, environment, profiles);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    setSaving(true);
    try {
      const saved = await updateAccountPoolEnvironment(accessToken, environment.id, form);
      onSaved(saved);
      onOpenChange(false);
      toast.success("号池配置已保存");
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 503 &&
        /saved|保存/i.test(error.message) &&
        /gateway|网关|synchron|同步/i.test(error.message)
      ) {
        try {
          const saved = await getAccountPoolEnvironment(accessToken, environment.id);
          onSaved(saved);
        } catch {
          onRefresh();
        }
        toast.warning("配置已保存，网关同步待完成", { description: "后台会继续重试同步，请稍后刷新状态" });
      } else {
        toast.fromError(error);
        onRefresh();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && saving) return;
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>配置号池环境</DialogTitle>
          <DialogDescription>调整环境状态、并发、出站代理和可用模型。</DialogDescription>
        </DialogHeader>
        {lifecycleDisabled && (
          <p
            className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground"
            role="status"
          >
            当前状态暂不支持修改配置，请等待生命周期操作完成
          </p>
        )}
        {
          <div className="grid gap-6">
            <div className="grid gap-2">
              <Label htmlFor="account-pool-config-name">名称</Label>
              <Input
                id="account-pool-config-name"
                value={form.name}
                onChange={(event) => update("name", event.target.value)}
                disabled={saving || lifecycleDisabled}
                maxLength={80}
              />
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="account-pool-concurrency">并发数</Label>
                <Input
                  id="account-pool-concurrency"
                  type="number"
                  min={1}
                  max={1000}
                  value={form.concurrency_limit}
                  onChange={(event) =>
                    update("concurrency_limit", Math.max(1, Math.min(1000, Number(event.target.value) || 1)))
                  }
                  disabled={saving || lifecycleDisabled}
                />
              </div>
              <div className="flex items-end justify-between gap-3 rounded-md border border-border px-3 py-2">
                <div>
                  <Label htmlFor="account-pool-enabled">环境开关</Label>
                  <p className="mt-1 text-xs text-muted-foreground">关闭后不会进入网关路由</p>
                </div>
                <Switch
                  id="account-pool-enabled"
                  checked={form.enabled}
                  onCheckedChange={(checked) => update("enabled", checked === true)}
                  disabled={saving || lifecycleDisabled}
                />
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-3">
              <div>
                <Label htmlFor="account-pool-cooldown">人工冷却</Label>
                <p className="mt-1 text-xs text-muted-foreground">手动暂停请求，取消后按实际健康状态恢复</p>
              </div>
              <Switch
                id="account-pool-cooldown"
                checked={form.manual_cooldown}
                onCheckedChange={(checked) => update("manual_cooldown", checked === true)}
                disabled={saving || lifecycleDisabled}
              />
            </div>
            <div className="grid gap-2">
              <Label>出站代理</Label>
              <Select value={form.proxy_mode} onValueChange={handleProxyModeChange}>
                <SelectTrigger className="w-full" disabled={saving || lifecycleDisabled}>
                  <SelectValue placeholder="选择代理模式" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default_gateway">默认网关</SelectItem>
                  <SelectItem value="profile">代理层 Profile</SelectItem>
                </SelectContent>
              </Select>
              {form.proxy_mode === "profile" && (
                <>
                  {profilesLoading && <p className="text-xs text-muted-foreground">正在加载代理 Profile...</p>}
                  {profilesError && (
                    <div className="flex items-center justify-between gap-2" role="alert">
                      <p className="text-xs text-destructive">{profilesError}</p>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => void profilesQuery.refetch()}
                        disabled={saving || profilesLoading}
                      >
                        重试
                      </Button>
                    </div>
                  )}
                  {!profilesLoading && !profilesError && profiles.length === 0 && (
                    <p className="text-xs text-destructive">暂无可用代理 Profile，请切换为默认网关或先配置 Profile</p>
                  )}
                  {!profilesLoading && !profilesError && profileSelectionError && profiles.length > 0 && (
                    <p className="text-xs text-destructive" role="alert">
                      {profileSelectionError}
                    </p>
                  )}
                  <Select value={form.proxy_profile_id} onValueChange={(value) => update("proxy_profile_id", value)}>
                    <SelectTrigger
                      className="w-full"
                      disabled={saving || lifecycleDisabled || profilesLoading || profiles.length === 0}
                    >
                      <SelectValue placeholder="选择代理 Profile" />
                    </SelectTrigger>
                    <SelectContent>
                      {profiles.map((profile) => (
                        <SelectItem key={profile.id} value={profile.id}>
                          {profile.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </>
              )}
            </div>
            <div className="grid gap-2">
              <Label>启用模型</Label>
              <div className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-2">
                {environment.available_models.map((model) => {
                  const checked = form.enabled_models.includes(model);
                  return (
                    <label key={model} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={checked}
                        disabled={saving || lifecycleDisabled}
                        onCheckedChange={(next) =>
                          update(
                            "enabled_models",
                            next === true
                              ? [...form.enabled_models, model]
                              : form.enabled_models.filter((enabledModel) => enabledModel !== model),
                          )
                        }
                      />
                      <span className="truncate">{model}</span>
                    </label>
                  );
                })}
                {environment.available_models.length === 0 && (
                  <p className="text-sm text-muted-foreground">暂无可用模型</p>
                )}
              </div>
            </div>
            <div className="grid gap-3 rounded-md border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">当前额度</p>
                  <p className="text-xs text-muted-foreground">最紧张窗口：{formatQuota(quotaWindow)}</p>
                </div>
                <p className="text-sm text-muted-foreground">重置于 {formatDateTime(quotaWindow?.resets_at)}</p>
              </div>
              {rows.map((row) => (
                <div key={row.key} className="grid gap-2">
                  <div className="flex justify-between text-xs">
                    <span>{row.label}</span>
                    <span>{row.quota.windows.length ? formatQuota(row.quota.windows[0]) : "尚未观测"}</span>
                  </div>
                  {row.quota.windows.map((window) => (
                    <div
                      key={`${row.key}-${window.name}`}
                      className="flex justify-between text-xs text-muted-foreground"
                    >
                      <span>{window.name}</span>
                      <span>
                        {formatQuota(window)}，{formatDateTime(window.resets_at)} 重置
                      </span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                取消
              </Button>
              <Button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving || lifecycleDisabled || profilesLoading || !form.name.trim()}
              >
                {saving ? "保存中..." : "保存配置"}
              </Button>
            </DialogFooter>
          </div>
        }
      </DialogContent>
    </Dialog>
  );
};
