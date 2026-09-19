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
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";

import { listAccountPoolProxyProfiles } from "@/app/(dashboard)/account-pool/AccountPoolApi";
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
} from "@/app/(dashboard)/account-pool/AccountPoolManagementApi";
import { NumberSetting, ToggleSetting } from "./RuntimeSettingsFields";
import { RuntimeSettingsProfileSection, type SettingsProfile } from "./RuntimeSettingsProfileSection";
import type { AccountPoolEnvironment } from "@/app/(dashboard)/account-pool/AccountPoolTypes";

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
  full_log_success_enabled: true,
  full_log_sample_percent: 100,
  full_log_max_body_kb: 16384,
  full_log_max_storage_mb: 0,
  daily_log_max_rows: 0,
  log_redact_fields: [],
  runtime_log_level: "inherit",
  runtime_log_format: "inherit",
  runtime_log_console: true,
  runtime_log_file: false,
  runtime_log_max_mb: 100,
  runtime_log_backups: 5,
  runtime_log_stacktrace: true,
  runtime_log_quiet_dependencies: true,
  full_logging_enabled: false,
  full_log_skip_failed: false,
  daily_log_retention_days: 30,
  full_log_retention_days: 30,
  auth_refresh_interval_minutes: 15,
  quota_refresh_interval_minutes: 5,
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

export type RuntimeSettingsCategory = "common" | "access" | "network" | "quota" | "streaming" | "advanced" | "payload";
const oauthAliasChannels = ["codex", "claude", "antigravity", "kimi", "xai", "gemini", "vertex", "aistudio"] as const;

type Props = {
  accessToken: string;
  category: RuntimeSettingsCategory;
  environments: readonly AccountPoolEnvironment[];
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

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

export const RuntimeSettingsSection = ({ accessToken, environments, category }: Props) => {
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
      const saved = await updateAccountPoolSettings(accessToken, { version, values }, category);
      await settingsQuery.refetch();
      setDraft(null);
      setJsonTexts({});
      setInvalidEditorIds([]);
      toast.success(
        t(saved.requires_reload ? "accountPool.settings.savedReloadRequired" : "accountPool.settings.saved"),
      );
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
      <p className="text-sm text-muted-foreground sm:col-span-2">
        卡片之间的选择由 LiteLLM 负载均衡、路由组和回退设置控制。
      </p>
      <NumberSetting
        id={`${editorId}-concurrency`}
        label={t("accountPool.settings.defaultConcurrency")}
        value={current.default_concurrency_limit}
        disabled={disabled || current.default_concurrency_limit === 0}
        onChange={(default_concurrency_limit) => onChange({ ...current, default_concurrency_limit })}
      />
      <ToggleSetting
        id={`${editorId}-unlimited-concurrency`}
        label={t("accountPool.settings.unlimitedConcurrency")}
        checked={current.default_concurrency_limit === 0}
        disabled={disabled}
        onChange={(unlimited) => onChange({ ...current, default_concurrency_limit: unlimited ? 0 : 1 })}
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
          items={[
            { value: "default", label: t("accountPool.config.defaultGateway") },
            ...(profilesQuery.data ?? []).map((profile) => ({ value: profile.id, label: profile.name })),
          ]}
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
      <p className="text-sm text-muted-foreground">
        重试次数使用本页 LiteLLM 原生策略，每个候选部署最多重试 4 次；已输出内容或工具调用不重放。
      </p>
      <NumberSetting
        id={`${editorId}-timeout`}
        label={t("accountPool.settings.timeout")}
        value={current.request_timeout_seconds}
        disabled={disabled}
        onChange={(request_timeout_seconds) => onChange({ ...current, request_timeout_seconds })}
      />
    </div>
  );

  const renderQuota = (current: QuotaSettingsValues) => (
    <div className="space-y-3 rounded-md border p-3 text-sm">
      <p>
        额度不足自动切换项目和预览模型目前不生效。为避免上游隐式切换和重复请求，执行时固定关闭；旧值保留供核对。跨卡回退请使用
        LiteLLM 路由设置。
      </p>
      <p>
        {t("accountPool.settings.quotaSwitchProject")}：{current.quota_switch_project ? "旧值开启，当前不执行" : "关闭"}
      </p>
      <p>
        {t("accountPool.settings.quotaSwitchPreview")}：
        {current.quota_switch_preview_model ? "旧值开启，当前不执行" : "关闭"}
      </p>
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
          <h2 className="text-lg font-semibold">{t(`accountPool.settings.categories.${category}`)}</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            按卡片保存供应商运行参数，未配置的卡片继承全局值。模型权限、预算与护栏使用 LiteLLM 原生配置。
          </p>
        </div>
        <Button type="button" onClick={() => void save()} disabled={busy}>
          {t("accountPool.settings.save")}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          {category === "common" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "access" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "network" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "quota" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "streaming" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "advanced" && (
            <RuntimeSettingsProfileSection
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
          )}

          {category === "payload" && (
            <RuntimeSettingsProfileSection
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
          )}
        </CardContent>
      </Card>
    </div>
  );
};
