/** 本文件编辑号池全局设置及各子模块的命名配置，并在显式保存后统一生效。 */

"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";

import { listAccountPoolProxyProfiles } from "./AccountPoolApi";
import {
  getAccountPoolSettings,
  updateAccountPoolSettings,
  type AccessSettingsValues,
  type AccountPoolSettings,
  type AdvancedSettingsValues,
  type CommonSettingsValues,
  type NetworkSettingsValues,
  type PayloadSettings,
  type QuotaSettingsValues,
  type StreamingSettingsValues,
} from "./AccountPoolManagementApi";
import { AccountPoolSettingsProfileSection, type SettingsProfile } from "./AccountPoolSettingsProfileSection";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const emptyPayload: PayloadSettings = {
  default: [],
  "default-raw": [],
  override: [],
  "override-raw": [],
  filter: [],
};

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
  payload: emptyPayload,
  plugins_enabled: false,
  streaming_enabled: true,
  common_profiles: [],
  access_profiles: [],
  network_profiles: [],
  quota_profiles: [],
  streaming_profiles: [],
  advanced_profiles: [],
  payload_profiles: [],
  streaming_rules: [],
};

const categories = ["common", "access", "network", "logging", "quota", "streaming", "advanced", "payload"] as const;
const oauthAliasChannels = ["codex", "claude", "antigravity", "kimi", "xai", "gemini", "vertex", "aistudio"] as const;
const routingModes = ["auto", "priority", "random", "quota"] as const;

type Props = {
  accessToken: string;
  environments: readonly AccountPoolEnvironment[];
};

type FieldProps = {
  id: string;
  label: string;
  disabled: boolean;
};

type JsonEditorProps<TValue> = {
  editorId: string;
  label: string;
  value: TValue;
  disabled: boolean;
  onChange: (value: TValue) => void;
  placeholder: string;
  className: string;
};

const NumberSetting = ({
  id,
  label,
  value,
  disabled,
  onChange,
}: FieldProps & { value: number; onChange: (value: number) => void }) => (
  <div className="grid gap-2 rounded-md border p-3">
    <Label htmlFor={id}>{label}</Label>
    <Input
      id={id}
      type="number"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  </div>
);

const ToggleSetting = ({
  id,
  label,
  checked,
  disabled,
  onChange,
}: FieldProps & { checked: boolean; onChange: (checked: boolean) => void }) => (
  <div className="flex min-h-16 items-center justify-between gap-4 rounded-md border p-3">
    <Label htmlFor={id} className="leading-5">
      {label}
    </Label>
    <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={(value) => onChange(value === true)} />
  </div>
);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isRoutingMode = (value: unknown): value is CommonSettingsValues["default_route"] =>
  routingModes.some((mode) => mode === value);

const normalizeProfiles = <TValues,>(
  profiles: ReadonlyArray<{
    id: string;
    name: string;
    card_ids: string[];
    inherit_global: boolean;
    values?: TValues;
  }>,
  fallback: TValues,
): SettingsProfile<TValues>[] => profiles.map((profile) => ({ ...profile, values: profile.values ?? fallback }));

export const AccountPoolSettingsPanel = ({ accessToken, environments }: Props) => {
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
  const [draft, setDraft] = useState<AccountPoolSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [jsonTexts, setJsonTexts] = useState<Record<string, string>>({});
  const [invalidEditorIds, setInvalidEditorIds] = useState<string[]>([]);

  const version = settingsQuery.data?.version ?? 0;
  const values = draft ?? settingsQuery.data?.values ?? defaults;
  const update = <K extends keyof AccountPoolSettings>(key: K, value: AccountPoolSettings[K]) =>
    setDraft((current) => ({ ...(current ?? settingsQuery.data?.values ?? defaults), [key]: value }));
  const patchValues = (patch: Partial<AccountPoolSettings>) =>
    setDraft((current) => ({ ...(current ?? settingsQuery.data?.values ?? defaults), ...patch }));
  const markEditor = (editorId: string, valid: boolean) =>
    setInvalidEditorIds((current) => {
      if (valid) return current.filter((identifier) => identifier !== editorId);
      if (current.includes(editorId)) return current;
      return [...current, editorId];
    });
  const updateJson = <TValue,>(editorId: string, text: string, onChange: (value: TValue) => void) => {
    setJsonTexts((current) => ({ ...current, [editorId]: text }));
    try {
      const parsed: unknown = JSON.parse(text);
      if (!isRecord(parsed)) {
        markEditor(editorId, false);
        return;
      }
      markEditor(editorId, true);
      onChange(parsed as TValue);
    } catch {
      markEditor(editorId, false);
    }
  };
  const clearProfileEditors = (profileId: string) => {
    setJsonTexts((current) =>
      Object.fromEntries(Object.entries(current).filter(([editorId]) => !editorId.includes(profileId))),
    );
    setInvalidEditorIds((current) => current.filter((editorId) => !editorId.includes(profileId)));
  };
  const allProfiles = [
    ...(values.common_profiles ?? []),
    ...(values.access_profiles ?? []),
    ...(values.network_profiles ?? []),
    ...(values.quota_profiles ?? []),
    ...(values.streaming_profiles ?? []),
    ...(values.advanced_profiles ?? []),
    ...(values.payload_profiles ?? []),
  ];
  const save = async () => {
    if (invalidEditorIds.length > 0) {
      toast.error(t("accountPool.settings.invalidJsonEditors"));
      return;
    }
    if (allProfiles.some((profile) => !profile.name.trim())) {
      toast.error(t("accountPool.settings.configurationNameRequired"));
      return;
    }
    setBusy(true);
    try {
      await updateAccountPoolSettings(accessToken, { version, values });
      await settingsQuery.refetch();
      setDraft(null);
      setJsonTexts({});
      setInvalidEditorIds([]);
      toast.success(t("accountPool.settings.saved"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };

  const commonGlobal: CommonSettingsValues = {
    default_route: values.default_route,
    default_concurrency_limit: values.default_concurrency_limit,
    default_model_discovery: values.default_model_discovery,
  };
  const accessGlobal: AccessSettingsValues = {
    oauth_excluded_models: values.oauth_excluded_models,
    oauth_model_aliases: values.oauth_model_aliases ?? {},
    oauth_request_scoped_errors: values.oauth_request_scoped_errors ?? {},
  };
  const networkGlobal: NetworkSettingsValues = {
    default_proxy_profile_id: values.default_proxy_profile_id,
    max_attempts: values.max_attempts,
    request_timeout_seconds: values.request_timeout_seconds,
    websocket_enabled: values.websocket_enabled,
  };
  const quotaGlobal: QuotaSettingsValues = {
    quota_switch_project: values.quota_switch_project,
    quota_switch_preview_model: values.quota_switch_preview_model,
  };
  const streamingGlobal: StreamingSettingsValues = { enabled: values.streaming_enabled };
  const advancedGlobal: AdvancedSettingsValues = {
    plugins_enabled: values.plugins_enabled,
    websocket_auth_enabled: values.websocket_auth_enabled,
    force_model_prefix: values.force_model_prefix,
    request_retry: values.request_retry,
    max_retry_credentials: values.max_retry_credentials,
    max_retry_interval: values.max_retry_interval,
  };
  const payloadGlobal = values.payload ?? emptyPayload;
  const commonProfiles = normalizeProfiles(values.common_profiles ?? [], commonGlobal);
  const accessProfiles = normalizeProfiles(values.access_profiles ?? [], accessGlobal);
  const networkProfiles = normalizeProfiles(values.network_profiles ?? [], networkGlobal);
  const quotaProfiles = normalizeProfiles(values.quota_profiles ?? [], quotaGlobal);
  const streamingProfiles = normalizeProfiles(
    (values.streaming_profiles ?? []).length > 0
      ? values.streaming_profiles
      : (values.streaming_rules ?? []).map((rule) => ({
          id: rule.id,
          name: rule.name,
          card_ids: rule.card_ids,
          inherit_global: false,
          values: { enabled: rule.mode === "enabled" },
        })),
    streamingGlobal,
  );
  const advancedProfiles = normalizeProfiles(values.advanced_profiles ?? [], advancedGlobal);
  const payloadProfiles = normalizeProfiles(values.payload_profiles ?? [], payloadGlobal);

  const renderJsonEditor = <TValue,>({
    editorId,
    label,
    value,
    disabled,
    onChange,
    placeholder,
    className,
  }: JsonEditorProps<TValue>) => (
    <div className="grid gap-2 rounded-md border p-3 sm:col-span-2">
      <Label htmlFor={editorId}>{label}</Label>
      <Textarea
        id={editorId}
        value={jsonTexts[editorId] ?? JSON.stringify(value, null, 2)}
        disabled={disabled}
        onChange={(event) => updateJson(editorId, event.target.value, onChange)}
        className={className}
        placeholder={placeholder}
        aria-invalid={invalidEditorIds.includes(editorId)}
      />
      {invalidEditorIds.includes(editorId) && (
        <p className="text-sm text-destructive" role="alert">
          {t("accountPool.settings.invalidJsonObject")}
        </p>
      )}
    </div>
  );

  const renderCommon = (
    current: CommonSettingsValues,
    onChange: (next: CommonSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid gap-2 rounded-md border p-3">
        <Label>{t("accountPool.settings.defaultRoute")}</Label>
        <Select
          value={current.default_route}
          disabled={disabled}
          onValueChange={(route) => {
            if (isRoutingMode(route)) onChange({ ...current, default_route: route });
          }}
        >
          <SelectTrigger className="w-full" aria-label={t("accountPool.settings.defaultRoute")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {routingModes.map((route) => (
              <SelectItem key={route} value={route}>
                {route}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <NumberSetting
        id={`${editorId}-concurrency`}
        label={t("accountPool.settings.defaultConcurrency")}
        value={current.default_concurrency_limit}
        disabled={disabled}
        onChange={(default_concurrency_limit) => onChange({ ...current, default_concurrency_limit })}
      />
      <ToggleSetting
        id={`${editorId}-model-discovery`}
        label={t("accountPool.settings.modelDiscovery")}
        checked={current.default_model_discovery}
        disabled={disabled}
        onChange={(default_model_discovery) => onChange({ ...current, default_model_discovery })}
      />
    </div>
  );

  const renderAccess = (
    current: AccessSettingsValues,
    onChange: (next: AccessSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid gap-2 rounded-md border p-3 sm:col-span-2">
        <Label htmlFor={`${editorId}-excluded-models`}>{t("accountPool.settings.oauthExcludedModels")}</Label>
        <Textarea
          id={`${editorId}-excluded-models`}
          value={current.oauth_excluded_models.join("\n")}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              ...current,
              oauth_excluded_models: event.target.value
                .split(/\r?\n|,/)
                .map((item) => item.trim())
                .filter(Boolean),
            })
          }
          placeholder={t("accountPool.settings.oauthExcludedModelsPlaceholder")}
          className="min-h-24"
        />
      </div>
      <div className="grid gap-3 rounded-md border p-3 sm:col-span-2">
        <div>
          <Label>{t("accountPool.settings.oauthModelAliases")}</Label>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.settings.oauthModelAliasesDescription")}</p>
        </div>
        {oauthAliasChannels.map((channel) => {
          const aliases = current.oauth_model_aliases?.[channel] ?? [];
          const updateAliases = (nextAliases: [string, string][]) =>
            onChange({
              ...current,
              oauth_model_aliases: { ...(current.oauth_model_aliases ?? {}), [channel]: nextAliases },
            });
          return (
            <div key={channel} className="grid gap-2 rounded-md border bg-muted/10 p-3">
              <div className="flex items-center justify-between gap-2 border-b pb-2">
                <span className="text-sm font-medium">{channel}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => updateAliases([...aliases, ["", ""]])}
                >
                  {t("accountPool.settings.addAlias")}
                </Button>
              </div>
              {aliases.map(([name, alias], index) => (
                <div key={`${channel}-${index}`} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                  <Input
                    value={name}
                    disabled={disabled}
                    placeholder={t("accountPool.settings.upstreamModel")}
                    onChange={(event) =>
                      updateAliases(
                        aliases.map((item, itemIndex) => (itemIndex === index ? [event.target.value, item[1]] : item)),
                      )
                    }
                  />
                  <Input
                    value={alias}
                    disabled={disabled}
                    placeholder={t("accountPool.settings.publicModel")}
                    onChange={(event) =>
                      updateAliases(
                        aliases.map((item, itemIndex) => (itemIndex === index ? [item[0], event.target.value] : item)),
                      )
                    }
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    onClick={() => updateAliases(aliases.filter((_, itemIndex) => itemIndex !== index))}
                  >
                    {t("accountPool.settings.removeAlias")}
                  </Button>
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {renderJsonEditor({
        editorId: `${editorId}-oauth-errors`,
        label: t("accountPool.settings.oauthRequestScopedErrors"),
        value: current.oauth_request_scoped_errors ?? {},
        disabled,
        onChange: (oauth_request_scoped_errors) => onChange({ ...current, oauth_request_scoped_errors }),
        placeholder: '{"codex":[{"status":400,"match":["context_window_exceeded"],"action":"stop"}]}',
        className: "min-h-36 font-mono text-xs",
      })}
    </div>
  );

  const renderNetwork = (
    current: NetworkSettingsValues,
    onChange: (next: NetworkSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid gap-2 rounded-md border p-3">
        <Label>{t("accountPool.settings.defaultProxy")}</Label>
        <Select
          value={current.default_proxy_profile_id ?? "default"}
          disabled={disabled}
          onValueChange={(profileId) =>
            typeof profileId === "string" &&
            onChange({ ...current, default_proxy_profile_id: profileId === "default" ? null : profileId })
          }
        >
          <SelectTrigger className="w-full" aria-label={t("accountPool.settings.defaultProxy")}>
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
      <NumberSetting
        id={`${editorId}-max-attempts`}
        label={t("accountPool.settings.maxAttempts")}
        value={current.max_attempts}
        disabled={disabled}
        onChange={(max_attempts) => onChange({ ...current, max_attempts })}
      />
      <NumberSetting
        id={`${editorId}-timeout`}
        label={t("accountPool.settings.timeout")}
        value={current.request_timeout_seconds}
        disabled={disabled}
        onChange={(request_timeout_seconds) => onChange({ ...current, request_timeout_seconds })}
      />
      <ToggleSetting
        id={`${editorId}-websocket`}
        label={t("accountPool.settings.websocket")}
        checked={current.websocket_enabled}
        disabled={disabled}
        onChange={(websocket_enabled) => onChange({ ...current, websocket_enabled })}
      />
    </div>
  );

  const renderQuota = (
    current: QuotaSettingsValues,
    onChange: (next: QuotaSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3 sm:grid-cols-2">
      <ToggleSetting
        id={`${editorId}-switch-project`}
        label={t("accountPool.settings.quotaSwitchProject")}
        checked={current.quota_switch_project}
        disabled={disabled}
        onChange={(quota_switch_project) => onChange({ ...current, quota_switch_project })}
      />
      <ToggleSetting
        id={`${editorId}-switch-preview`}
        label={t("accountPool.settings.quotaSwitchPreview")}
        checked={current.quota_switch_preview_model}
        disabled={disabled}
        onChange={(quota_switch_preview_model) => onChange({ ...current, quota_switch_preview_model })}
      />
    </div>
  );

  const renderStreaming = (
    current: StreamingSettingsValues,
    onChange: (next: StreamingSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3">
      <ToggleSetting
        id={`${editorId}-enabled`}
        label={t("accountPool.settings.streamingEnabled")}
        checked={current.enabled}
        disabled={disabled}
        onChange={(enabled) => onChange({ enabled })}
      />
      <p className="text-xs text-muted-foreground">{t("accountPool.settings.streamingDescription")}</p>
    </div>
  );

  const renderAdvanced = (
    current: AdvancedSettingsValues,
    onChange: (next: AdvancedSettingsValues) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3 sm:grid-cols-2">
      <ToggleSetting
        id={`${editorId}-plugins`}
        label={t("accountPool.settings.plugins")}
        checked={current.plugins_enabled}
        disabled={disabled}
        onChange={(plugins_enabled) => onChange({ ...current, plugins_enabled })}
      />
      <ToggleSetting
        id={`${editorId}-websocket-auth`}
        label={t("accountPool.settings.websocketAuth")}
        checked={current.websocket_auth_enabled}
        disabled={disabled}
        onChange={(websocket_auth_enabled) => onChange({ ...current, websocket_auth_enabled })}
      />
      <ToggleSetting
        id={`${editorId}-model-prefix`}
        label={t("accountPool.settings.forceModelPrefix")}
        checked={current.force_model_prefix}
        disabled={disabled}
        onChange={(force_model_prefix) => onChange({ ...current, force_model_prefix })}
      />
      <NumberSetting
        id={`${editorId}-request-retry`}
        label={t("accountPool.settings.requestRetry")}
        value={current.request_retry}
        disabled={disabled}
        onChange={(request_retry) => onChange({ ...current, request_retry })}
      />
      <NumberSetting
        id={`${editorId}-retry-credentials`}
        label={t("accountPool.settings.maxRetryCredentials")}
        value={current.max_retry_credentials}
        disabled={disabled}
        onChange={(max_retry_credentials) => onChange({ ...current, max_retry_credentials })}
      />
      <NumberSetting
        id={`${editorId}-retry-interval`}
        label={t("accountPool.settings.maxRetryInterval")}
        value={current.max_retry_interval}
        disabled={disabled}
        onChange={(max_retry_interval) => onChange({ ...current, max_retry_interval })}
      />
    </div>
  );

  const renderPayload = (
    current: PayloadSettings,
    onChange: (next: PayloadSettings) => void,
    disabled: boolean,
    editorId: string,
  ) => (
    <div className="grid gap-3">
      <p className="text-xs text-muted-foreground">{t("accountPool.settings.payloadDescription")}</p>
      {renderJsonEditor({
        editorId: `${editorId}-payload`,
        label: t("accountPool.settings.payloadConfiguration"),
        value: current,
        disabled,
        onChange,
        placeholder: '{"default":[],"default-raw":[],"override":[],"override-raw":[],"filter":[]}',
        className: "min-h-72 font-mono text-xs",
      })}
    </div>
  );

  if (settingsQuery.isPending && settingsQuery.data === undefined) {
    return <p className="text-sm text-muted-foreground">{t("accountPool.settings.loading")}</p>;
  }
  if (settingsQuery.isError) {
    return (
      <div className="grid gap-3 rounded-lg border p-4">
        <p className="text-sm text-destructive">{t("accountPool.settings.loadFailed")}</p>
        <Button type="button" variant="outline" className="w-fit" onClick={() => void settingsQuery.refetch()}>
          {t("accountPool.settings.retry")}
        </Button>
      </div>
    );
  }

  return (
    <div className="grid gap-5" data-testid="account-pool-settings-panel">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <h2 className="text-lg font-semibold">{t("accountPool.settings.title")}</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("accountPool.settings.description")}</p>
        </div>
        <Button type="button" onClick={() => void save()} disabled={busy}>
          {t("accountPool.settings.save")}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Tabs defaultValue="common">
            <TabsList className="h-auto w-full justify-start overflow-x-auto">
              {categories.map((category) => (
                <TabsTrigger key={category} value={category}>
                  {t(`accountPool.settings.categories.${category}`)}
                </TabsTrigger>
              ))}
            </TabsList>

            <TabsContent value="common">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.common")}
                globalValues={commonGlobal}
                profiles={commonProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => patchValues(next)}
                onProfilesChange={(profiles) => update("common_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderCommon}
              />
            </TabsContent>

            <TabsContent value="access">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.access")}
                globalValues={accessGlobal}
                profiles={accessProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => patchValues(next)}
                onProfilesChange={(profiles) => update("access_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderAccess}
              />
            </TabsContent>

            <TabsContent value="network">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.network")}
                globalValues={networkGlobal}
                profiles={networkProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => patchValues(next)}
                onProfilesChange={(profiles) => update("network_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderNetwork}
              />
            </TabsContent>

            <TabsContent value="logging">
              <div className="grid gap-5 pt-4">
                <section className="grid gap-4 rounded-lg border bg-muted/15 p-4">
                  <div>
                    <h3 className="text-sm font-semibold">{t("accountPool.settings.globalConfiguration")}</h3>
                    <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.settings.loggingGlobalOnly")}</p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <ToggleSetting
                      id="logging-file"
                      label={t("accountPool.settings.fileLogging")}
                      checked={values.file_logging_enabled}
                      disabled={busy}
                      onChange={(file_logging_enabled) => update("file_logging_enabled", file_logging_enabled)}
                    />
                    <ToggleSetting
                      id="logging-debug"
                      label={t("accountPool.settings.debugLogging")}
                      checked={values.debug_logging_enabled}
                      disabled={busy}
                      onChange={(debug_logging_enabled) => update("debug_logging_enabled", debug_logging_enabled)}
                    />
                    <ToggleSetting
                      id="logging-request"
                      label={t("accountPool.settings.requestLog")}
                      checked={values.request_log_enabled}
                      disabled={busy}
                      onChange={(request_log_enabled) => update("request_log_enabled", request_log_enabled)}
                    />
                    <ToggleSetting
                      id="logging-usage"
                      label={t("accountPool.settings.usageStatistics")}
                      checked={values.usage_statistics_enabled}
                      disabled={busy}
                      onChange={(usage_statistics_enabled) =>
                        update("usage_statistics_enabled", usage_statistics_enabled)
                      }
                    />
                    <NumberSetting
                      id="logging-size"
                      label={t("accountPool.settings.logsMaxSize")}
                      value={values.logs_max_total_size_mb}
                      disabled={busy}
                      onChange={(logs_max_total_size_mb) => update("logs_max_total_size_mb", logs_max_total_size_mb)}
                    />
                    <NumberSetting
                      id="logging-files"
                      label={t("accountPool.settings.errorLogsMaxFiles")}
                      value={values.error_logs_max_files}
                      disabled={busy}
                      onChange={(error_logs_max_files) => update("error_logs_max_files", error_logs_max_files)}
                    />
                  </div>
                </section>
                <div className="flex justify-end border-t pt-4">
                  <Button type="button" disabled={busy} onClick={() => void save()}>
                    {t("accountPool.settings.save")}
                  </Button>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="quota">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.quota")}
                globalValues={quotaGlobal}
                profiles={quotaProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => patchValues(next)}
                onProfilesChange={(profiles) => update("quota_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderQuota}
              />
            </TabsContent>

            <TabsContent value="streaming">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.streaming")}
                globalValues={streamingGlobal}
                profiles={streamingProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => update("streaming_enabled", next.enabled)}
                onProfilesChange={(profiles) => patchValues({ streaming_profiles: profiles, streaming_rules: [] })}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderStreaming}
              />
            </TabsContent>

            <TabsContent value="advanced">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.advanced")}
                globalValues={advancedGlobal}
                profiles={advancedProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => patchValues(next)}
                onProfilesChange={(profiles) => update("advanced_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderAdvanced}
              />
            </TabsContent>

            <TabsContent value="payload">
              <AccountPoolSettingsProfileSection
                moduleName={t("accountPool.settings.categories.payload")}
                globalValues={payloadGlobal}
                profiles={payloadProfiles}
                environments={environments}
                busy={busy}
                onGlobalChange={(next) => update("payload", next)}
                onProfilesChange={(profiles) => update("payload_profiles", profiles)}
                onProfileDeleted={clearProfileEditors}
                onSave={() => void save()}
                renderValues={renderPayload}
              />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
};
