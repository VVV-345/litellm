/** 本文件提供号池全局配置面板，支持分类编辑、差异预览、版本历史和回滚。 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/lib/toast";

import {
  getAccountPoolSettings,
  listAccountPoolSettingsHistory,
  previewAccountPoolSettings,
  rollbackAccountPoolSettings,
  updateAccountPoolSettings,
  type AccountPoolSettings,
} from "./AccountPoolManagementApi";

const defaults: AccountPoolSettings = {
  default_route: "auto",
  default_concurrency_limit: 1,
  default_model_discovery: true,
  default_proxy_profile_id: null,
  max_attempts: 1,
  request_timeout_seconds: 120,
  file_logging_enabled: false,
  debug_logging_enabled: false,
  websocket_enabled: false,
  plugins_enabled: false,
};

type Props = {
  accessToken: string;
};

export const AccountPoolSettingsPanel = ({ accessToken }: Props) => {
  const { t } = useTranslation();
  const settingsQuery = useQuery({
    queryKey: ["account-pool", "settings", accessToken],
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
  });
  const historyQuery = useQuery({
    queryKey: ["account-pool", "settings-history", accessToken],
    queryFn: () => listAccountPoolSettingsHistory(accessToken),
    retry: false,
  });
  const [draft, setDraft] = useState<AccountPoolSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewText, setPreviewText] = useState<string[]>([]);

  const version = settingsQuery.data?.version ?? 0;
  const values = draft ?? settingsQuery.data?.values ?? defaults;
  const history = historyQuery.data ?? [];
  const update = <K extends keyof AccountPoolSettings>(key: K, value: AccountPoolSettings[K]) => {
    setDraft((current) => ({ ...(current ?? settingsQuery.data?.values ?? defaults), [key]: value }));
  };
  const runPreview = async () => {
    setBusy(true);
    try {
      const result = await previewAccountPoolSettings(accessToken, { version, values });
      setPreviewText(result.changes.map((change) => `${change.key}: ${change.previous} -> ${change.proposed}`));
      toast.success(t("accountPool.settings.previewReady"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setBusy(true);
    try {
      await updateAccountPoolSettings(accessToken, { version, values });
      await Promise.all([settingsQuery.refetch(), historyQuery.refetch()]);
      setDraft(null);
      setPreviewText([]);
      toast.success(t("accountPool.settings.saved"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  const rollback = async (targetVersion: number) => {
    setBusy(true);
    try {
      await rollbackAccountPoolSettings(accessToken, version, targetVersion);
      await Promise.all([settingsQuery.refetch(), historyQuery.refetch()]);
      setDraft(null);
      setPreviewText([]);
      toast.success(t("accountPool.settings.rolledBack"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  const categories = useMemo(
    () => ["common", "access", "network", "logging", "quota", "streaming", "advanced", "payload"],
    [],
  );
  const textField = <K extends keyof AccountPoolSettings>(key: K, label: string) => (
    <div className="grid gap-1" key={String(key)}>
      <Label htmlFor={`account-pool-setting-${String(key)}`}>{label}</Label>
      <Input
        id={`account-pool-setting-${String(key)}`}
        value={String(values[key] ?? "")}
        disabled={busy}
        onChange={(event) => update(key, event.target.value as AccountPoolSettings[K])}
      />
    </div>
  );
  const numberField = <K extends keyof AccountPoolSettings>(key: K, label: string) => (
    <div className="grid gap-1" key={String(key)}>
      <Label htmlFor={`account-pool-setting-${String(key)}`}>{label}</Label>
      <Input
        id={`account-pool-setting-${String(key)}`}
        type="number"
        value={Number(values[key])}
        disabled={busy}
        onChange={(event) => update(key, Number(event.target.value) as AccountPoolSettings[K])}
      />
    </div>
  );
  const toggleField = <K extends keyof AccountPoolSettings>(key: K, label: string) => (
    <div className="flex items-center justify-between gap-3 rounded-md border p-3" key={String(key)}>
      <Label htmlFor={`account-pool-setting-${String(key)}`}>{label}</Label>
      <Switch
        id={`account-pool-setting-${String(key)}`}
        checked={Boolean(values[key])}
        disabled={busy}
        onCheckedChange={(checked) => update(key, (checked === true) as AccountPoolSettings[K])}
      />
    </div>
  );

  return (
    <div className="grid gap-5" data-testid="account-pool-settings-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.settings.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.settings.description")}</p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={() => void runPreview()} disabled={busy || settingsQuery.isPending}>
            {t("accountPool.settings.preview")}
          </Button>
          <Button type="button" onClick={() => void save()} disabled={busy || settingsQuery.isPending}>
            {t("accountPool.settings.save")}
          </Button>
        </div>
      </div>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("accountPool.settings.version", { version })}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <Tabs defaultValue="common">
            <TabsList className="h-auto w-full justify-start overflow-x-auto">
              {categories.map((category) => (
                <TabsTrigger key={category} value={category}>{t(`accountPool.settings.categories.${category}`)}</TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value="common" className="grid gap-3 pt-4 sm:grid-cols-2">
              <div className="grid gap-1">
                <Label>{t("accountPool.settings.defaultRoute")}</Label>
                <Select value={values.default_route} disabled={busy} onValueChange={(value) => value && update("default_route", value as AccountPoolSettings["default_route"])}>
                  <SelectTrigger aria-label={t("accountPool.settings.defaultRoute")}><SelectValue /></SelectTrigger>
                  <SelectContent>{["auto", "priority", "random", "quota"].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              {numberField("default_concurrency_limit", t("accountPool.settings.defaultConcurrency"))}
              {toggleField("default_model_discovery", t("accountPool.settings.modelDiscovery"))}
              {textField("default_proxy_profile_id", t("accountPool.settings.defaultProxy"))}
            </TabsContent>
            <TabsContent value="access" className="grid gap-3 pt-4 sm:grid-cols-2">
              <p className="text-sm text-muted-foreground sm:col-span-2">{t("accountPool.settings.accessDescription")}</p>
              {textField("default_proxy_profile_id", t("accountPool.settings.defaultProxy"))}
            </TabsContent>
            <TabsContent value="network" className="grid gap-3 pt-4 sm:grid-cols-2">
              {numberField("max_attempts", t("accountPool.settings.maxAttempts"))}
              {numberField("request_timeout_seconds", t("accountPool.settings.timeout"))}
              {toggleField("websocket_enabled", t("accountPool.settings.websocket"))}
            </TabsContent>
            <TabsContent value="logging" className="grid gap-3 pt-4 sm:grid-cols-2">
              {toggleField("file_logging_enabled", t("accountPool.settings.fileLogging"))}
              {toggleField("debug_logging_enabled", t("accountPool.settings.debugLogging"))}
            </TabsContent>
            <TabsContent value="quota" className="pt-4"><p className="text-sm text-muted-foreground">{t("accountPool.settings.quotaDescription")}</p></TabsContent>
            <TabsContent value="streaming" className="pt-4"><p className="text-sm text-muted-foreground">{t("accountPool.settings.streamingDescription")}</p></TabsContent>
            <TabsContent value="advanced" className="grid gap-3 pt-4">{toggleField("plugins_enabled", t("accountPool.settings.plugins"))}</TabsContent>
            <TabsContent value="payload" className="pt-4"><p className="text-sm text-muted-foreground">{t("accountPool.settings.payloadDescription")}</p></TabsContent>
          </Tabs>
          {previewText.length > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm" role="status">
              <p className="font-medium">{t("accountPool.settings.previewChanges")}</p>
              <ul className="mt-2 list-disc pl-5">{previewText.map((change) => <li key={change}>{change}</li>)}</ul>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3"><CardTitle className="text-base">{t("accountPool.settings.history")}</CardTitle></CardHeader>
        <CardContent>
          {history.length === 0 ? <p className="text-sm text-muted-foreground">{t("accountPool.settings.historyEmpty")}</p> : (
            <div className="grid gap-2">{history.map((entry) => (
              <div key={`${entry.version}-${entry.created_at}`} className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm">
                <span>{t("accountPool.settings.historyVersion", { version: entry.version, source: entry.source })}</span>
                <Button type="button" variant="outline" size="sm" onClick={() => void rollback(entry.version)} disabled={busy || entry.version === version}>{t("accountPool.settings.rollback")}</Button>
              </div>
            ))}</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
