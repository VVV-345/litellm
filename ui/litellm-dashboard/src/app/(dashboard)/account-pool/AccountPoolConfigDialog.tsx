/** 本文件编辑已授权号池环境的运行配置，并将表单变更一次性提交给管理 API。 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

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
import { canConfigureEnvironment } from "./AccountPoolPermissions";
import {
  concurrencyLimitLabel,
  formatDateTime,
  formatQuota,
  mostConstrainedWindow,
  quotaRows,
} from "./AccountPoolFormatters";
import { validateAccountPoolUpdate, validateProxyProfileSelection } from "./AccountPoolValidation";
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
  const { t, i18n } = useTranslation();
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
  const profilesError = profilesQuery.isError ? t("accountPool.config.proxyProfilesLoadFailed") : null;
  const profileSelectionError =
    form.proxy_mode === "profile" && !profilesError
      ? validateProxyProfileSelection(t, form.proxy_mode, form.proxy_profile_id, profiles)
      : null;

  const quotaWindow = useMemo(() => mostConstrainedWindow(environment), [environment]);
  const rows = useMemo(() => quotaRows(t, environment), [environment, t]);
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
    const validationError = validateAccountPoolUpdate(t, form, environment, profiles);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    setSaving(true);
    try {
      const saved = await updateAccountPoolEnvironment(accessToken, environment.id, form);
      onSaved(saved);
      onOpenChange(false);
      toast.success(t("accountPool.config.saved"));
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
        toast.warning(t("accountPool.config.savedPendingSync"), {
          description: t("accountPool.config.savedPendingSyncDescription"),
        });
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
          <DialogTitle>{t("accountPool.config.title")}</DialogTitle>
          <DialogDescription>{t("accountPool.config.description")}</DialogDescription>
        </DialogHeader>
        {lifecycleDisabled && (
          <p
            className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground"
            role="status"
          >
            {t("accountPool.config.lifecycleDisabled")}
          </p>
        )}
        {
          <div className="grid gap-6">
            <div className="grid gap-2">
              <Label htmlFor="account-pool-config-name">{t("accountPool.config.name")}</Label>
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
                <Label htmlFor="account-pool-concurrency">{concurrencyLimitLabel(t)}</Label>
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
                  <Label htmlFor="account-pool-enabled">{t("accountPool.config.environmentSwitch")}</Label>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("accountPool.config.environmentSwitchDescription")}
                  </p>
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
                <Label htmlFor="account-pool-cooldown">{t("accountPool.config.manualCooldown")}</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t("accountPool.config.manualCooldownDescription")}
                </p>
              </div>
              <Switch
                id="account-pool-cooldown"
                checked={form.manual_cooldown}
                onCheckedChange={(checked) => update("manual_cooldown", checked === true)}
                disabled={saving || lifecycleDisabled}
              />
            </div>
            <div className="grid gap-2">
              <Label>{t("accountPool.config.outboundProxy")}</Label>
              <Select value={form.proxy_mode} onValueChange={handleProxyModeChange}>
                <SelectTrigger className="w-full" disabled={saving || lifecycleDisabled}>
                  <SelectValue placeholder={t("accountPool.config.selectProxyMode")} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default_gateway">{t("accountPool.config.defaultGateway")}</SelectItem>
                  <SelectItem value="profile">{t("accountPool.config.proxyProfile")}</SelectItem>
                </SelectContent>
              </Select>
              {form.proxy_mode === "profile" && (
                <>
                  {profilesLoading && (
                    <p className="text-xs text-muted-foreground">{t("accountPool.config.loadingProxyProfiles")}</p>
                  )}
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
                        {t("accountPool.retry")}
                      </Button>
                    </div>
                  )}
                  {!profilesLoading && !profilesError && profiles.length === 0 && (
                    <p className="text-xs text-destructive">{t("accountPool.config.noProxyProfiles")}</p>
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
                      <SelectValue placeholder={t("accountPool.config.selectProxyProfile")} />
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
              <Label>{t("accountPool.config.enabledModels")}</Label>
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
                  <p className="text-sm text-muted-foreground">{t("accountPool.config.noAvailableModels")}</p>
                )}
              </div>
            </div>
            <div className="grid gap-3 rounded-md border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">{t("accountPool.config.currentQuota")}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("accountPool.config.mostConstrainedWindow", { quota: formatQuota(t, quotaWindow) })}
                  </p>
                </div>
                <p className="text-sm text-muted-foreground">
                  {t("accountPool.config.resetsAt", {
                    time: formatDateTime(quotaWindow?.resets_at, i18n.language),
                  })}
                </p>
              </div>
              {rows.map((row) => (
                <div key={row.key} className="grid gap-2">
                  <div className="flex justify-between text-xs">
                    <span>{row.label}</span>
                    <span>
                      {row.quota.windows.length
                        ? formatQuota(t, row.quota.windows[0])
                        : t("accountPool.config.notObserved")}
                    </span>
                  </div>
                  {row.quota.windows.map((window) => (
                    <div
                      key={`${row.key}-${window.name}`}
                      className="flex justify-between text-xs text-muted-foreground"
                    >
                      <span>{window.name}</span>
                      <span>
                        {formatQuota(t, window)}，{formatDateTime(window.resets_at, i18n.language)}{" "}
                        {t("accountPool.config.reset")}
                      </span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                {t("accountPool.cancel")}
              </Button>
              <Button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving || lifecycleDisabled || profilesLoading || !form.name.trim()}
              >
                {saving ? t("accountPool.config.saving") : t("accountPool.config.save")}
              </Button>
            </DialogFooter>
          </div>
        }
      </DialogContent>
    </Dialog>
  );
};
