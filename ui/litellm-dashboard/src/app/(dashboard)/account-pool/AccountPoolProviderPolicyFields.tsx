/** 本文件渲染各供应商独有的卡片策略字段。 */

import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AccountPolicy } from "./AccountPoolManagementApi";

type Codex = NonNullable<AccountPolicy["codex"]>;
type Claude = NonNullable<AccountPolicy["claude"]>;
type Kimi = NonNullable<AccountPolicy["kimi"]>;
type Xai = NonNullable<AccountPolicy["xai"]>;
type Antigravity = NonNullable<AccountPolicy["antigravity"]>;

interface AccountPoolProviderPolicyFieldsProps {
  supplier: string;
  busy: boolean;
  codex: Codex;
  claude: Claude;
  kimi: Kimi;
  xai: Xai;
  antigravity: Antigravity;
  clients: string;
  sensitiveWords: string;
  onClientsChange: (value: string) => void;
  onSensitiveWordsChange: (value: string) => void;
  onCodexChange: <K extends keyof Codex>(field: K, value: Codex[K]) => void;
  onClaudeChange: <K extends keyof Claude>(field: K, value: Claude[K]) => void;
  onKimiChange: <K extends keyof Kimi>(field: K, value: Kimi[K]) => void;
  onXaiChange: <K extends keyof Xai>(field: K, value: Xai[K]) => void;
  onAntigravityChange: <K extends keyof Antigravity>(field: K, value: Antigravity[K]) => void;
}

export function AccountPoolProviderPolicyFields({
  supplier,
  busy,
  codex,
  claude,
  kimi,
  xai,
  antigravity,
  clients,
  sensitiveWords,
  onClientsChange,
  onSensitiveWordsChange,
  onCodexChange,
  onClaudeChange,
  onKimiChange,
  onXaiChange,
  onAntigravityChange,
}: AccountPoolProviderPolicyFieldsProps) {
  const { t } = useTranslation();
  const textField = (label: string, value: string, onChange: (next: string) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Input aria-label={label} value={value} disabled={busy} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
  const toggleField = (label: string, checked: boolean, onChange: (next: boolean) => void) => (
    <div className="flex items-center justify-between gap-3 rounded-md border p-3">
      <Label>{label}</Label>
      <Switch
        aria-label={label}
        checked={checked}
        disabled={busy}
        onCheckedChange={(next) => onChange(next === true)}
      />
    </div>
  );
  const selectField = (label: string, value: string, options: readonly string[], onChange: (next: string) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Select value={value} disabled={busy} onValueChange={(next) => next !== null && onChange(next)}>
        <SelectTrigger aria-label={label}>
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
  );

  switch (supplier) {
    case "openai_codex":
      return (
        <>
          {textField(t("accountPool.policy.allow_app_server_clients"), clients, onClientsChange)}
          {toggleField(t("accountPool.policy.cli_only"), codex.cli_only, (next) => onCodexChange("cli_only", next))}
          {toggleField(t("accountPool.policy.allow_app_server"), codex.allow_app_server, (next) =>
            onCodexChange("allow_app_server", next),
          )}
          {toggleField(t("accountPool.policy.responses_compact_enabled"), codex.responses_compact_enabled, (next) =>
            onCodexChange("responses_compact_enabled", next),
          )}
          {toggleField(t("accountPool.policy.identity_confuse"), codex.identity_confuse, (next) =>
            onCodexChange("identity_confuse", next),
          )}
          {toggleField(t("accountPool.policy.disable_codex_cloaking"), codex.disable_codex_cloaking, (next) =>
            onCodexChange("disable_codex_cloaking", next),
          )}
        </>
      );
    case "anthropic_claude":
      return (
        <>
          {selectField(
            t("accountPool.policy.fingerprint_profile"),
            claude.fingerprint_profile,
            ["inherit", "claude-code-cli"],
            (next) => onClaudeChange("fingerprint_profile", next as Claude["fingerprint_profile"]),
          )}
          {selectField(t("accountPool.policy.cloak_mode"), claude.cloak_mode, ["auto", "always", "never"], (next) =>
            onClaudeChange("cloak_mode", next as Claude["cloak_mode"]),
          )}
          {toggleField(t("accountPool.policy.rebuild_mid_system_message"), claude.rebuild_mid_system_message, (next) =>
            onClaudeChange("rebuild_mid_system_message", next),
          )}
        </>
      );
    case "kimi":
      return (
        <>
          {selectField(
            t("accountPool.policy.fingerprint_profile"),
            kimi.fingerprint_profile,
            ["inherit", "claude-code-cli"],
            (next) => onKimiChange("fingerprint_profile", next as Kimi["fingerprint_profile"]),
          )}
          <p className="rounded-md border bg-muted/30 p-3 text-sm">{t("accountPool.policy.kimiDeviceIdAuto")}</p>
        </>
      );
    case "xai":
      return toggleField(t("accountPool.policy.inject_x_search"), xai.inject_x_search, (next) =>
        onXaiChange("inject_x_search", next),
      );
    case "google_antigravity":
      return (
        <>
          {textField(t("accountPool.policy.sensitive_words"), sensitiveWords, onSensitiveWordsChange)}
          {toggleField(t("accountPool.policy.signature_cache_enabled"), antigravity.signature_cache_enabled, (next) =>
            onAntigravityChange("signature_cache_enabled", next),
          )}
          {toggleField(t("accountPool.policy.signature_bypass_strict"), antigravity.signature_bypass_strict, (next) =>
            onAntigravityChange("signature_bypass_strict", next),
          )}
        </>
      );
    default:
      return <p className="text-sm text-muted-foreground sm:col-span-2">{t("accountPool.policy.providerPending")}</p>;
  }
}
