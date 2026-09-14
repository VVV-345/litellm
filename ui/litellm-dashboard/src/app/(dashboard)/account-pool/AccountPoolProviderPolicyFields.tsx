/** 渲染认证摘要和供应商独有的单卡策略字段。 */

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { MultiSelect } from "@/components/shared/MultiSelect";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

import { formatDateTime, formatQuota, mostConstrainedWindow } from "./AccountPoolFormatters";
import type { AccountPolicy } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { accountPoolPlanLabel } from "./accountPoolCodexPlan";
import type { AccountPoolPolicyOption } from "./accountPoolPolicyOptions";

type Codex = NonNullable<AccountPolicy["codex"]>;
type Claude = NonNullable<AccountPolicy["claude"]>;
type Kimi = NonNullable<AccountPolicy["kimi"]>;
type Xai = NonNullable<AccountPolicy["xai"]>;
type Antigravity = NonNullable<AccountPolicy["antigravity"]>;

interface AccountPoolProviderPolicyFieldsProps {
  environment: AccountPoolEnvironment;
  busy: boolean;
  codex: Codex;
  claude: Claude;
  kimi: Kimi;
  xai: Xai;
  antigravity: Antigravity;
  codexClientOptions: AccountPoolPolicyOption[];
  sensitiveWordOptions: AccountPoolPolicyOption[];
  onCodexChange: <K extends keyof Codex>(field: K, value: Codex[K]) => void;
  onClaudeChange: <K extends keyof Claude>(field: K, value: Claude[K]) => void;
  onKimiChange: <K extends keyof Kimi>(field: K, value: Kimi[K]) => void;
  onXaiChange: <K extends keyof Xai>(field: K, value: Xai[K]) => void;
  onAntigravityChange: <K extends keyof Antigravity>(field: K, value: Antigravity[K]) => void;
}

interface ToggleFieldProps {
  label: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}

interface SelectFieldProps {
  label: string;
  description: string;
  value: string;
  options: readonly string[];
  onChange: (next: string) => void;
}

interface MultiFieldProps {
  label: string;
  description: string;
  value: string[];
  options: AccountPoolPolicyOption[];
  onChange: (next: string[]) => void;
}

const FieldCard = ({ children }: { children: ReactNode }) => (
  <div className="min-w-0 rounded-lg border border-border bg-background p-4">{children}</div>
);

export function AccountPoolProviderPolicyFields({
  environment,
  busy,
  codex,
  claude,
  kimi,
  xai,
  antigravity,
  codexClientOptions,
  sensitiveWordOptions,
  onCodexChange,
  onClaudeChange,
  onKimiChange,
  onXaiChange,
  onAntigravityChange,
}: AccountPoolProviderPolicyFieldsProps) {
  const { t, i18n } = useTranslation();
  const quotaWindow = mostConstrainedWindow(environment);
  const quota = environment.quota;
  const planLabel =
    environment.supplier === "openai_codex"
      ? accountPoolPlanLabel(quota.plan_type, quota.auth_file_plan_type)
      : quota.plan_type || "-";
  const toggleField = ({ label, description, checked, onChange, disabled = false }: ToggleFieldProps) => (
    <FieldCard>
      <div className="flex min-w-0 items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <Label className="block break-words leading-5">{label}</Label>
          <p className="break-words text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
        <Switch
          aria-label={label}
          checked={checked}
          disabled={busy || disabled}
          onCheckedChange={(next) => onChange(next === true)}
        />
      </div>
    </FieldCard>
  );
  const selectField = ({ label, description, value, options, onChange }: SelectFieldProps) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <div className="space-y-1">
          <Label className="block break-words leading-5">{label}</Label>
          <p className="break-words text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
        <Select value={value} disabled={busy} onValueChange={(next) => next !== null && onChange(next)}>
          <SelectTrigger aria-label={label} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`accountPool.policy.options.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </FieldCard>
  );
  const multiField = ({ label, description, value, options, onChange }: MultiFieldProps) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <div className="space-y-1">
          <Label className="block break-words leading-5">{label}</Label>
          <p className="break-words text-xs leading-5 text-muted-foreground">{description}</p>
        </div>
        <MultiSelect
          value={value}
          options={Array.from(
            new Map(
              [...options, ...value.map((item) => ({ label: item, value: item }))].map((item) => [item.value, item]),
            ).values(),
          )}
          onValueChange={onChange}
          allowCustomValues
          disabled={busy}
          placeholder={t("accountPool.policy.selectOrCreate")}
          emptyText={t("accountPool.policy.noMatchingOptions")}
        />
      </div>
    </FieldCard>
  );
  const summary = (
    <div className="grid gap-3 rounded-lg border border-border bg-muted/30 p-4 sm:grid-cols-3 lg:col-span-2">
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{t("accountPool.policy.subscriptionTier")}</p>
        <Badge className="mt-2 max-w-full truncate" variant="secondary">
          {planLabel}
        </Badge>
      </div>
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{t("accountPool.policy.remainingQuota")}</p>
        <p className="mt-2 break-words font-medium">{formatQuota(t, quotaWindow)}</p>
      </div>
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{t("accountPool.policy.subscriptionExpiry")}</p>
        <p className="mt-2 break-words font-medium">{formatDateTime(quota.subscription_active_until, i18n.language)}</p>
      </div>
      <p className="text-xs leading-5 text-muted-foreground sm:col-span-3">
        {t("accountPool.policy.subscriptionReadOnly")}
      </p>
    </div>
  );

  switch (environment.supplier) {
    case "openai_codex":
      return (
        <>
          {summary}
          {selectField({
            label: t("accountPool.policy.identity_fingerprint_mode"),
            description: t("accountPool.policy.identityFingerprintModeDescription"),
            value: codex.identity_fingerprint_mode,
            options: ["off", "device", "session", "full"],
            onChange: (next) => onCodexChange("identity_fingerprint_mode", next as Codex["identity_fingerprint_mode"]),
          })}
          {toggleField({
            label: t("accountPool.policy.cli_only"),
            description: t("accountPool.policy.cliOnlyDescription"),
            checked: codex.cli_only,
            onChange: (next) => {
              onCodexChange("cli_only", next);
              if (!next) onCodexChange("allow_app_server", false);
            },
          })}
          {toggleField({
            label: t("accountPool.policy.allow_app_server"),
            description: t("accountPool.policy.allowAppServerDescription"),
            checked: codex.allow_app_server,
            disabled: !codex.cli_only,
            onChange: (next) => onCodexChange("allow_app_server", next),
          })}
          {multiField({
            label: t("accountPool.policy.allow_app_server_clients"),
            description: t("accountPool.policy.appServerClientsDescription"),
            value: codex.allow_app_server_clients,
            options: codexClientOptions,
            onChange: (next) => onCodexChange("allow_app_server_clients", next),
          })}
          {toggleField({
            label: t("accountPool.policy.responses_compact_enabled"),
            description: t("accountPool.policy.responsesCompactDescription"),
            checked: codex.responses_compact_enabled,
            onChange: (next) => onCodexChange("responses_compact_enabled", next),
          })}
          {toggleField({
            label: t("accountPool.policy.identity_confuse"),
            description: t("accountPool.policy.identityConfuseDescription"),
            checked: codex.identity_confuse,
            onChange: (next) => onCodexChange("identity_confuse", next),
          })}
          {toggleField({
            label: t("accountPool.policy.disable_codex_cloaking"),
            description: t("accountPool.policy.disableCodexCloakingDescription"),
            checked: codex.disable_codex_cloaking,
            onChange: (next) => onCodexChange("disable_codex_cloaking", next),
          })}
        </>
      );
    case "anthropic_claude":
      return (
        <>
          {summary}
          {selectField({
            label: t("accountPool.policy.fingerprint_profile"),
            description: t("accountPool.policy.claudeFingerprintDescription"),
            value: claude.fingerprint_profile,
            options: ["inherit", "claude-code-cli"],
            onChange: (next) => onClaudeChange("fingerprint_profile", next as Claude["fingerprint_profile"]),
          })}
          {selectField({
            label: t("accountPool.policy.cloak_mode"),
            description: t("accountPool.policy.claudeCloakDescription"),
            value: claude.cloak_mode,
            options: ["auto", "always", "never"],
            onChange: (next) => onClaudeChange("cloak_mode", next as Claude["cloak_mode"]),
          })}
          {toggleField({
            label: t("accountPool.policy.rebuild_mid_system_message"),
            description: t("accountPool.policy.rebuildSystemDescription"),
            checked: claude.rebuild_mid_system_message,
            onChange: (next) => onClaudeChange("rebuild_mid_system_message", next),
          })}
        </>
      );
    case "kimi":
      return (
        <>
          {summary}
          {selectField({
            label: t("accountPool.policy.fingerprint_profile"),
            description: t("accountPool.policy.kimiFingerprintDescription"),
            value: kimi.fingerprint_profile,
            options: ["inherit", "claude-code-cli"],
            onChange: (next) => onKimiChange("fingerprint_profile", next as Kimi["fingerprint_profile"]),
          })}
          <p className="rounded-lg border bg-muted/30 p-4 text-sm leading-6 lg:col-span-2">
            {t("accountPool.policy.kimiDeviceIdAuto")}
          </p>
        </>
      );
    case "xai":
      return (
        <>
          {summary}
          {toggleField({
            label: t("accountPool.policy.inject_x_search"),
            description: t("accountPool.policy.xSearchDescription"),
            checked: xai.inject_x_search,
            onChange: (next) => onXaiChange("inject_x_search", next),
          })}
        </>
      );
    case "google_antigravity":
      return (
        <>
          {summary}
          {multiField({
            label: t("accountPool.policy.sensitive_words"),
            description: t("accountPool.policy.sensitiveWordsDescription"),
            value: antigravity.sensitive_words,
            options: sensitiveWordOptions,
            onChange: (next) => onAntigravityChange("sensitive_words", next),
          })}
          {toggleField({
            label: t("accountPool.policy.signature_cache_enabled"),
            description: t("accountPool.policy.signatureCacheDescription"),
            checked: antigravity.signature_cache_enabled,
            onChange: (next) => onAntigravityChange("signature_cache_enabled", next),
          })}
          {toggleField({
            label: t("accountPool.policy.signature_bypass_strict"),
            description: t("accountPool.policy.signatureBypassDescription"),
            checked: antigravity.signature_bypass_strict,
            onChange: (next) => onAntigravityChange("signature_bypass_strict", next),
          })}
        </>
      );
    default:
      return (
        <>
          {summary}
          <p className="rounded-lg border bg-muted/30 p-4 text-sm leading-6 text-muted-foreground lg:col-span-2">
            {t("accountPool.policy.providerPending")}
          </p>
        </>
      );
  }
}
