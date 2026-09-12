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
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";

import {
  getAccountPoolSettings,
  listAccountPoolSettingsHistory,
  previewAccountPoolSettings,
  rollbackAccountPoolSettings,
  updateAccountPoolSettings,
  type AccountPoolSettings,
} from "./AccountPoolManagementApi";
import { listAccountPoolProxyProfiles } from "./AccountPoolApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { parseAccountPoolSettingsEditors } from "./AccountPoolSettingsEditors";

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
  request_log_enabled: false,
  websocket_auth_enabled: false,
  force_model_prefix: false,
  request_retry: 1,
  max_retry_credentials: 1,
  max_retry_interval: 0,
  usage_statistics_enabled: false,
  logs_max_total_size_mb: 0,
  error_logs_max_files: 10,
  quota_switch_project: false,
  quota_switch_preview_model: false,
  oauth_excluded_models: [],
  oauth_model_aliases: {},
  oauth_request_scoped_errors: {},
  payload: { default: [], "default-raw": [], override: [], "override-raw": [], filter: [] },
  plugins_enabled: false,
  streaming_rules: [],
};

const OAUTH_ALIAS_CHANNELS = ["codex", "claude", "antigravity", "kimi", "xai", "gemini", "vertex", "aistudio"] as const;

type Props = {
  accessToken: string;
};

export const AccountPoolSettingsPanel = ({
  accessToken,
  environments,
}: Props & { environments: readonly AccountPoolEnvironment[] }) => {
  const { t } = useTranslation();
  const settingsQuery = useQuery({
    queryKey: ["account-pool", "settings", accessToken],
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
  });
  const profilesQuery = useQuery({
    queryKey: ["account-pool", "proxy-profiles", accessToken],
    queryFn: () => listAccountPoolProxyProfiles(accessToken),
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
  const [payloadText, setPayloadText] = useState<string | null>(null);
  const [oauthErrorsText, setOauthErrorsText] = useState<string | null>(null);
  const [payloadInvalid, setPayloadInvalid] = useState(false);
  const [oauthErrorsInvalid, setOauthErrorsInvalid] = useState(false);

  const version = settingsQuery.data?.version ?? 0;
  const values = draft ?? settingsQuery.data?.values ?? defaults;
  const history = historyQuery.data ?? [];
  const update = <K extends keyof AccountPoolSettings>(key: K, value: AccountPoolSettings[K]) => {
    setDraft((current) => ({ ...(current ?? settingsQuery.data?.values ?? defaults), [key]: value }));
  };
  const currentValues = () => {
    const parsed = parseAccountPoolSettingsEditors(values, payloadText, oauthErrorsText);
    setPayloadInvalid(!parsed.ok && parsed.payloadInvalid);
    setOauthErrorsInvalid(!parsed.ok && parsed.oauthErrorsInvalid);
    return parsed.ok ? parsed.values : null;
  };
  const runPreview = async () => {
    const nextValues = currentValues();
    if (nextValues === null) return;
    setBusy(true);
    try {
      const result = await previewAccountPoolSettings(accessToken, { version, values: nextValues });
      setPreviewText(result.changes.map((change) => `${change.key}: ${change.previous} -> ${change.proposed}`));
      toast.success(t("accountPool.settings.previewReady"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    const nextValues = currentValues();
    if (nextValues === null) return;
    setBusy(true);
    try {
      await updateAccountPoolSettings(accessToken, { version, values: nextValues });
      await Promise.all([settingsQuery.refetch(), historyQuery.refetch()]);
      setDraft(null);
      setPreviewText([]);
      setPayloadText(null);
      setOauthErrorsText(null);
      setPayloadInvalid(false);
      setOauthErrorsInvalid(false);
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
      setPayloadText(null);
      setOauthErrorsText(null);
      setPayloadInvalid(false);
      setOauthErrorsInvalid(false);
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
    <div className="grid gap-1 border-b pb-3" key={String(key)}>
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
    <div className="grid gap-1 border-b pb-3" key={String(key)}>
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
  const updateOAuthAliases = (channel: string, aliases: Array<[string, string]>) =>
    update("oauth_model_aliases", { ...(values.oauth_model_aliases ?? {}), [channel]: aliases });

  return (
    <div className="grid gap-5" data-testid="account-pool-settings-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.settings.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.settings.description")}</p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => void runPreview()}
            disabled={busy || settingsQuery.isPending}
          >
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
                <TabsTrigger key={category} value={category}>
                  {t(`accountPool.settings.categories.${category}`)}
                </TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value="common" className="grid gap-3 pt-4 sm:grid-cols-2">
              <div className="grid gap-1">
                <Label>{t("accountPool.settings.defaultRoute")}</Label>
                <Select
                  value={values.default_route}
                  disabled={busy}
                  onValueChange={(value) =>
                    value && update("default_route", value as AccountPoolSettings["default_route"])
                  }
                >
                  <SelectTrigger aria-label={t("accountPool.settings.defaultRoute")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {["auto", "priority", "random", "quota"].map((value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {numberField("default_concurrency_limit", t("accountPool.settings.defaultConcurrency"))}
              {toggleField("default_model_discovery", t("accountPool.settings.modelDiscovery"))}
              <div className="grid gap-1" key="default_proxy_profile_id">
                <Label>{t("accountPool.settings.defaultProxy")}</Label>
                <Select
                  value={values.default_proxy_profile_id ?? "default"}
                  disabled={busy}
                  onValueChange={(value) => update("default_proxy_profile_id", value === "default" ? null : value)}
                >
                  <SelectTrigger aria-label={t("accountPool.settings.defaultProxy")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">{t("accountPool.config.defaultGateway")}</SelectItem>
                    {(profilesQuery.data ?? []).map((profile) => (
                      <SelectItem key={profile.id} value={profile.id}>
                        {profile.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </TabsContent>
            <TabsContent value="access" className="grid gap-3 pt-4 sm:grid-cols-2">
              <p className="text-sm text-muted-foreground sm:col-span-2">
                {t("accountPool.settings.accessDescription")}
              </p>
              <div className="grid gap-1" key="access-default-proxy">
                <Label>{t("accountPool.settings.defaultProxy")}</Label>
                <Select
                  value={values.default_proxy_profile_id ?? "default"}
                  disabled={busy}
                  onValueChange={(value) => update("default_proxy_profile_id", value === "default" ? null : value)}
                >
                  <SelectTrigger aria-label={t("accountPool.settings.defaultProxy")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="default">{t("accountPool.config.defaultGateway")}</SelectItem>
                    {(profilesQuery.data ?? []).map((profile) => (
                      <SelectItem key={profile.id} value={profile.id}>
                        {profile.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </TabsContent>
            <TabsContent value="network" className="grid gap-3 pt-4 sm:grid-cols-2">
              {numberField("max_attempts", t("accountPool.settings.maxAttempts"))}
              {numberField("request_timeout_seconds", t("accountPool.settings.timeout"))}
              {toggleField("websocket_enabled", t("accountPool.settings.websocket"))}
            </TabsContent>
            <TabsContent value="logging" className="grid gap-3 pt-4 sm:grid-cols-2">
              {toggleField("file_logging_enabled", t("accountPool.settings.fileLogging"))}
              {toggleField("debug_logging_enabled", t("accountPool.settings.debugLogging"))}
              {toggleField("request_log_enabled", t("accountPool.settings.requestLog"))}
              {toggleField("usage_statistics_enabled", t("accountPool.settings.usageStatistics"))}
              {numberField("logs_max_total_size_mb", t("accountPool.settings.logsMaxSize"))}
              {numberField("error_logs_max_files", t("accountPool.settings.errorLogsMaxFiles"))}
            </TabsContent>
            <TabsContent value="quota" className="grid gap-3 pt-4 sm:grid-cols-2">
              {toggleField("quota_switch_project", t("accountPool.settings.quotaSwitchProject"))}
              {toggleField("quota_switch_preview_model", t("accountPool.settings.quotaSwitchPreview"))}
            </TabsContent>
            <TabsContent value="streaming" className="grid gap-4 pt-4">
              <p className="text-sm text-muted-foreground">{t("accountPool.settings.streamingDescription")}</p>
              <div className="grid gap-3">
                {(values.streaming_rules ?? []).map((rule, index) => {
                  const assigned = new Set(
                    (values.streaming_rules ?? []).flatMap((item, itemIndex) =>
                      itemIndex === index ? [] : item.card_ids,
                    ),
                  );
                  return (
                    <div key={rule.id} className="grid gap-3 rounded-md border p-4">
                      <div className="flex items-center justify-between gap-3 border-b pb-3">
                        <Input
                          value={rule.name}
                          disabled={busy}
                          onChange={(event) =>
                            update(
                              "streaming_rules",
                              (values.streaming_rules ?? []).map((item, itemIndex) =>
                                itemIndex === index ? { ...item, name: event.target.value } : item,
                              ),
                            )
                          }
                          aria-label={t("accountPool.settings.ruleName")}
                        />
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            update(
                              "streaming_rules",
                              (values.streaming_rules ?? []).filter((_, itemIndex) => itemIndex !== index),
                            )
                          }
                        >
                          {t("accountPool.settings.removeRule")}
                        </Button>
                      </div>
                      <Select
                        value={rule.mode}
                        disabled={busy}
                        onValueChange={(mode) => {
                          if (mode !== "enabled" && mode !== "disabled") return;
                          update(
                            "streaming_rules",
                            (values.streaming_rules ?? []).map((item, itemIndex) =>
                              itemIndex === index ? { ...item, mode } : item,
                            ),
                          );
                        }}
                      >
                        <SelectTrigger aria-label={t("accountPool.settings.ruleMode")}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="enabled">{t("accountPool.settings.streamEnabled")}</SelectItem>
                          <SelectItem value="disabled">{t("accountPool.settings.streamDisabled")}</SelectItem>
                        </SelectContent>
                      </Select>
                      <div className="grid gap-2 border-t pt-3">
                        <Label>{t("accountPool.settings.applyCards")}</Label>
                        {environments.map((environment) => (
                          <label key={environment.id} className="flex items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              checked={rule.card_ids.includes(environment.id)}
                              disabled={
                                busy || (assigned.has(environment.id) && !rule.card_ids.includes(environment.id))
                              }
                              onChange={(event) =>
                                update(
                                  "streaming_rules",
                                  (values.streaming_rules ?? []).map((item, itemIndex) =>
                                    itemIndex === index
                                      ? {
                                          ...item,
                                          card_ids: event.target.checked
                                            ? [...item.card_ids, environment.id]
                                            : item.card_ids.filter((id) => id !== environment.id),
                                        }
                                      : item,
                                  ),
                                )
                              }
                            />
                            <span>{environment.name}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    update("streaming_rules", [
                      ...(values.streaming_rules ?? []),
                      {
                        id: crypto.randomUUID(),
                        name: `${t("accountPool.settings.ruleName")} ${(values.streaming_rules ?? []).length + 1}`,
                        mode: "enabled",
                        card_ids: [],
                      },
                    ])
                  }
                >
                  {t("accountPool.settings.addRule")}
                </Button>
              </div>
            </TabsContent>
            <TabsContent value="advanced" className="grid gap-3 pt-4 sm:grid-cols-2">
              {toggleField("plugins_enabled", t("accountPool.settings.plugins"))}
              {toggleField("websocket_auth_enabled", t("accountPool.settings.websocketAuth"))}
              {toggleField("force_model_prefix", t("accountPool.settings.forceModelPrefix"))}
              {numberField("request_retry", t("accountPool.settings.requestRetry"))}
              {numberField("max_retry_credentials", t("accountPool.settings.maxRetryCredentials"))}
              {numberField("max_retry_interval", t("accountPool.settings.maxRetryInterval"))}
              <div className="grid gap-1 sm:col-span-2">
                <Label htmlFor="account-pool-oauth-excluded-models">
                  {t("accountPool.settings.oauthExcludedModels")}
                </Label>
                <Textarea
                  id="account-pool-oauth-excluded-models"
                  value={(values.oauth_excluded_models ?? []).join("\n")}
                  disabled={busy}
                  onChange={(event) =>
                    update(
                      "oauth_excluded_models",
                      event.target.value
                        .split(/\r?\n|,/)
                        .map((item) => item.trim())
                        .filter(Boolean),
                    )
                  }
                  placeholder={t("accountPool.settings.oauthExcludedModelsPlaceholder")}
                  className="min-h-20"
                />
              </div>
              <div className="grid gap-3 sm:col-span-2">
                <div>
                  <Label>{t("accountPool.settings.oauthModelAliases")}</Label>
                  <p className="text-xs text-muted-foreground">
                    {t("accountPool.settings.oauthModelAliasesDescription")}
                  </p>
                </div>
                {OAUTH_ALIAS_CHANNELS.map((channel) => {
                  const aliases = values.oauth_model_aliases?.[channel] ?? [];
                  return (
                    <div key={channel} className="grid gap-2 rounded-md border p-3">
                      <div className="flex items-center justify-between gap-2 border-b pb-2">
                        <span className="text-sm font-medium">{channel}</span>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => updateOAuthAliases(channel, [...aliases, ["", ""]])}
                        >
                          {t("accountPool.settings.addAlias")}
                        </Button>
                      </div>
                      {aliases.map(([name, alias], index) => (
                        <div
                          key={`${channel}-${index}`}
                          className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                        >
                          <Input
                            value={name}
                            disabled={busy}
                            placeholder={t("accountPool.settings.upstreamModel")}
                            onChange={(event) =>
                              updateOAuthAliases(
                                channel,
                                aliases.map((item, itemIndex) =>
                                  itemIndex === index ? [event.target.value, item[1]] : item,
                                ),
                              )
                            }
                          />
                          <Input
                            value={alias}
                            disabled={busy}
                            placeholder={t("accountPool.settings.publicModel")}
                            onChange={(event) =>
                              updateOAuthAliases(
                                channel,
                                aliases.map((item, itemIndex) =>
                                  itemIndex === index ? [item[0], event.target.value] : item,
                                ),
                              )
                            }
                          />
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() =>
                              updateOAuthAliases(
                                channel,
                                aliases.filter((_, itemIndex) => itemIndex !== index),
                              )
                            }
                          >
                            {t("accountPool.settings.removeAlias")}
                          </Button>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
              <div className="grid gap-2 sm:col-span-2">
                <Label htmlFor="account-pool-oauth-errors">{t("accountPool.settings.oauthRequestScopedErrors")}</Label>
                <Textarea
                  id="account-pool-oauth-errors"
                  value={oauthErrorsText ?? JSON.stringify(values.oauth_request_scoped_errors ?? {}, null, 2)}
                  disabled={busy}
                  onChange={(event) => {
                    setOauthErrorsText(event.target.value);
                    setOauthErrorsInvalid(false);
                  }}
                  className="min-h-32 font-mono text-xs"
                  placeholder={'{"codex":[{"status":400,"match":["context_window_exceeded"],"action":"stop"}]}'}
                />
                {oauthErrorsInvalid && (
                  <p className="text-sm text-destructive" role="alert">
                    {t("accountPool.settings.invalidOAuthErrorsJson")}
                  </p>
                )}
              </div>
            </TabsContent>
            <TabsContent value="payload" className="grid gap-2 pt-4">
              <p className="text-sm text-muted-foreground">{t("accountPool.settings.payloadDescription")}</p>
              <Textarea
                aria-label={t("accountPool.settings.payloadDescription")}
                value={payloadText ?? JSON.stringify(values.payload ?? {}, null, 2)}
                disabled={busy}
                onChange={(event) => {
                  setPayloadText(event.target.value);
                  setPayloadInvalid(false);
                }}
                className="min-h-72 font-mono text-xs"
                placeholder={'{"default":[],"default-raw":[],"override":[],"override-raw":[],"filter":[]}'}
              />
              {payloadInvalid && (
                <p className="text-sm text-destructive" role="alert">
                  {t("accountPool.settings.invalidPayloadJson")}
                </p>
              )}
            </TabsContent>
          </Tabs>
          {previewText.length > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm" role="status">
              <p className="font-medium">{t("accountPool.settings.previewChanges")}</p>
              <ul className="mt-2 list-disc pl-5">
                {previewText.map((change) => (
                  <li key={change}>{change}</li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("accountPool.settings.history")}</CardTitle>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("accountPool.settings.historyEmpty")}</p>
          ) : (
            <div className="grid gap-2">
              {history.map((entry) => (
                <div
                  key={`${entry.version}-${entry.created_at}`}
                  className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm"
                >
                  <span>
                    {t("accountPool.settings.historyVersion", { version: entry.version, source: entry.source })}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void rollback(entry.version)}
                    disabled={busy || entry.version === version}
                  >
                    {t("accountPool.settings.rollback")}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
