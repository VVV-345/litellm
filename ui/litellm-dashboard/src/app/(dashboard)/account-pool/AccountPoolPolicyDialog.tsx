/** 本文件编辑版本化账号策略，区分管理元数据与尚待代理接入的设置。 */

import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/lib/toast";
import { getAccountPolicy, saveAccountPolicy, type AccountPolicy, type PolicyView } from "./AccountPoolManagementApi";
import { AccountPoolProviderPolicyFields } from "./AccountPoolProviderPolicyFields";

type Routing = NonNullable<AccountPolicy["routing"]>;
type Transport = NonNullable<AccountPolicy["transport"]>;
type Codex = NonNullable<AccountPolicy["codex"]>;
type Claude = NonNullable<AccountPolicy["claude"]>;
type Kimi = NonNullable<AccountPolicy["kimi"]>;
type Xai = NonNullable<AccountPolicy["xai"]>;
type Antigravity = NonNullable<AccountPolicy["antigravity"]>;
type FormPolicy = Omit<AccountPolicy, "routing" | "transport" | "codex" | "claude" | "kimi" | "xai" | "antigravity"> & {
  routing: Routing;
  transport: Transport;
  codex: Codex | null;
  claude: Claude | null;
  kimi: Kimi | null;
  xai: Xai | null;
  antigravity: Antigravity | null;
};

const codexDefaults: Codex = {
  cli_only: false,
  allow_app_server: false,
  allow_app_server_clients: [],
  responses_compact_enabled: false,
  identity_confuse: false,
  disable_codex_cloaking: false,
};
const claudeDefaults: Claude = {
  fingerprint_profile: "inherit",
  cloak_mode: "auto",
  rebuild_mid_system_message: false,
};
const kimiDefaults: Kimi = { fingerprint_profile: "inherit" };
const xaiDefaults: Xai = { inject_x_search: false };
const antigravityDefaults: Antigravity = {
  sensitive_words: [],
  signature_cache_enabled: true,
  signature_bypass_strict: false,
};
const defaults: FormPolicy = {
  tags: [],
  group: "",
  account_ids: [],
  excluded_models: [],
  model_aliases: [],
  routing: {
    strategy: "auto",
    priority: 0,
    weight: 1,
    is_backup: false,
    preferred_account_ids: [],
    session_affinity: false,
    session_affinity_ttl: 3600,
    quota_reserve_percent: 0,
    quota_snapshot_max_age: 300,
    token_budget_limit: null,
    token_budget_window_seconds: 3600,
    max_attempts: 1,
    retryable_statuses: [429, 502, 503, 504],
    backoff_ms: 1000,
    fallback_enabled: false,
  },
  transport: {
    image_generation: "inherit",
    websocket: "inherit",
    request_timeout_seconds: 120,
    debug_log_enabled: false,
  },
  codex: null,
  claude: null,
  kimi: null,
  xai: null,
  antigravity: null,
};

export function AccountPoolPolicyDialog({
  accessToken,
  cardId,
  name,
  supplier,
  onClose,
}: {
  accessToken: string;
  cardId: string;
  name: string;
  supplier: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryOptions = {
    queryKey: ["account-pool", "policy", accessToken, cardId],
    queryFn: () => getAccountPolicy(accessToken, cardId),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  };
  const query = useQuery(queryOptions);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("accountPool.policy.title", { name })}</DialogTitle>
          <DialogDescription>{t("accountPool.policy.description")}</DialogDescription>
        </DialogHeader>
        {query.isPending && <p>{t("accountPool.management.loading")}</p>}
        {query.isError && (
          <div role="alert">
            <p>{t("accountPool.policy.loadFailed")}</p>
            <Button onClick={() => void query.refetch()}>{t("accountPool.retry")}</Button>
          </div>
        )}
        {query.data && (
          <PolicyForm
            key={query.data.version}
            value={query.data}
            supplier={supplier}
            save={async (policy) => {
              await saveAccountPolicy(accessToken, cardId, query.data.version, policy);
              await query.refetch();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function PolicyForm({
  value,
  supplier,
  save,
}: {
  value: PolicyView;
  supplier: string;
  save: (policy: AccountPolicy) => Promise<void>;
}) {
  const { t } = useTranslation();
  const source = value.policy ?? defaults;
  const initial: FormPolicy = {
    ...defaults,
    ...source,
    routing: { ...defaults.routing, ...source.routing },
    transport: { ...defaults.transport, ...source.transport },
    codex: source.codex ?? null,
    claude: source.claude ?? null,
    kimi: source.kimi ?? null,
    xai: source.xai ?? null,
    antigravity: source.antigravity ?? null,
  };
  const [policy, setPolicy] = useState<FormPolicy>(initial);
  const [busy, setBusy] = useState(false);
  const [tags, setTags] = useState(policy.tags.join(", "));
  const [excluded, setExcluded] = useState(policy.excluded_models.join(", "));
  const [aliases, setAliases] = useState(policy.model_aliases.map((item) => `${item.alias}=${item.target}`).join(", "));
  const [preferred, setPreferred] = useState(policy.routing.preferred_account_ids.join(", "));
  const [members, setMembers] = useState((policy.account_ids ?? []).join(", "));
  const [statuses, setStatuses] = useState(policy.routing.retryable_statuses.join(", "));
  const codex = policy.codex ?? codexDefaults;
  const claude = policy.claude ?? claudeDefaults;
  const kimi = policy.kimi ?? kimiDefaults;
  const xai = policy.xai ?? xaiDefaults;
  const antigravity = policy.antigravity ?? antigravityDefaults;
  const [clients, setClients] = useState(codex.allow_app_server_clients.join(", "));
  const [sensitiveWords, setSensitiveWords] = useState(antigravity.sensitive_words.join(", "));
  const list = (input: string) =>
    input
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  const updateRouting = <K extends keyof Routing>(field: K, next: Routing[K]) =>
    setPolicy((current) => ({ ...current, routing: { ...current.routing, [field]: next } }));
  const updateTransport = <K extends keyof Transport>(field: K, next: Transport[K]) =>
    setPolicy((current) => ({ ...current, transport: { ...current.transport, [field]: next } }));
  const updateCodex = <K extends keyof Codex>(field: K, next: Codex[K]) =>
    setPolicy((current) => ({ ...current, codex: { ...(current.codex ?? codexDefaults), [field]: next } }));
  const updateClaude = <K extends keyof Claude>(field: K, next: Claude[K]) =>
    setPolicy((current) => ({ ...current, claude: { ...(current.claude ?? claudeDefaults), [field]: next } }));
  const updateKimi = <K extends keyof Kimi>(field: K, next: Kimi[K]) =>
    setPolicy((current) => ({ ...current, kimi: { ...(current.kimi ?? kimiDefaults), [field]: next } }));
  const updateXai = <K extends keyof Xai>(field: K, next: Xai[K]) =>
    setPolicy((current) => ({ ...current, xai: { ...(current.xai ?? xaiDefaults), [field]: next } }));
  const updateAntigravity = <K extends keyof Antigravity>(field: K, next: Antigravity[K]) =>
    setPolicy((current) => ({
      ...current,
      antigravity: { ...(current.antigravity ?? antigravityDefaults), [field]: next },
    }));
  const text = (label: string, current: string, change: (next: string) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Input aria-label={label} value={current} disabled={busy} onChange={(event) => change(event.target.value)} />
    </div>
  );
  const toggle = (label: string, checked: boolean, change: (next: boolean) => void) => (
    <div className="flex items-center justify-between gap-3 rounded-md border p-3">
      <Label>{label}</Label>
      <Switch aria-label={label} checked={checked} disabled={busy} onCheckedChange={(next) => change(next === true)} />
    </div>
  );
  const number = (label: string, value: number, change: (next: number) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Input
        aria-label={label}
        type="number"
        value={value}
        disabled={busy}
        onChange={(event) => change(Number(event.target.value))}
      />
    </div>
  );
  const optionalNumber = (label: string, value: number | null | undefined, change: (next: number | null) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Input
        aria-label={label}
        type="number"
        value={value ?? ""}
        disabled={busy}
        onChange={(event) => change(event.target.value ? Number(event.target.value) : null)}
      />
    </div>
  );
  const select = (label: string, value: string, options: readonly string[], change: (next: string) => void) => (
    <div className="grid gap-1 border-b pb-3">
      <Label>{label}</Label>
      <Select
        value={value}
        disabled={busy}
        onValueChange={(next) => {
          if (next !== null) change(next);
        }}
      >
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
  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const aliasValues = list(aliases);
    if (aliasValues.some((item) => !/^\S.*=\s*\S/.test(item))) {
      toast.error(t("accountPool.policy.invalidAliases"));
      return;
    }
    const parsed: FormPolicy = {
      ...policy,
      account_ids: list(members),
      codex: { ...codex, allow_app_server_clients: list(clients) },
      antigravity: { ...antigravity, sensitive_words: list(sensitiveWords) },
      tags: list(tags),
      excluded_models: list(excluded),
      model_aliases: aliasValues.map((item) => {
        const split = item.indexOf("=");
        return { alias: item.slice(0, split).trim(), target: item.slice(split + 1).trim() };
      }),
      routing: {
        ...policy.routing,
        preferred_account_ids: list(preferred) as Routing["preferred_account_ids"],
        retryable_statuses: list(statuses).map(Number),
      },
    };
    setBusy(true);
    try {
      const { codex, claude, kimi, xai, antigravity, ...rest } = parsed;
      await save({
        ...rest,
        ...(supplier === "openai_codex" && codex ? { codex } : {}),
        ...(supplier === "anthropic_claude" && claude ? { claude } : {}),
        ...(supplier === "kimi" && kimi ? { kimi } : {}),
        ...(supplier === "xai" && xai ? { xai } : {}),
        ...(supplier === "google_antigravity" && antigravity ? { antigravity } : {}),
      });
      toast.success(t("accountPool.policy.saved"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="grid gap-4" onSubmit={handleSubmit}>
      <p className="text-sm">{t("accountPool.policy.version", { version: value.version })}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {text(t("accountPool.policy.tags"), tags, setTags)}
        {text(t("accountPool.policy.group"), policy.group, (group) => setPolicy((current) => ({ ...current, group })))}
      </div>
      <p role="status" className="rounded-md border bg-muted/30 p-3 text-sm">
        {t("accountPool.policy.pending")}
      </p>
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        {value.capabilities?.map((capability) => (
          <div key={capability.name} className="flex justify-between gap-2">
            <dt>{t(`accountPool.policy.capabilities.${capability.name}`)}</dt>
            <dd>{t(`accountPool.policy.capabilityStatus.${capability.status}`)}</dd>
          </div>
        ))}
      </dl>
      {text(t("accountPool.policy.account_ids"), members, setMembers)}
      <fieldset className="grid gap-3 rounded-md border p-4 sm:grid-cols-2">
        <legend className="mb-3 font-medium">{t("accountPool.policy.routing")}</legend>
        {select(
          t("accountPool.policy.strategy"),
          policy.routing.strategy,
          ["auto", "random", "priority", "quota", "plan", "expiry", "custom"],
          (next) => updateRouting("strategy", next as Routing["strategy"]),
        )}
        {number(t("accountPool.policy.priority"), policy.routing.priority, (next) => updateRouting("priority", next))}
        {number(t("accountPool.policy.weight"), policy.routing.weight, (next) => updateRouting("weight", next))}
        {number(t("accountPool.policy.session_affinity_ttl"), policy.routing.session_affinity_ttl, (next) =>
          updateRouting("session_affinity_ttl", next),
        )}
        {number(t("accountPool.policy.quota_reserve_percent"), policy.routing.quota_reserve_percent, (next) =>
          updateRouting("quota_reserve_percent", next),
        )}
        {number(t("accountPool.policy.quota_snapshot_max_age"), policy.routing.quota_snapshot_max_age, (next) =>
          updateRouting("quota_snapshot_max_age", next),
        )}
        {optionalNumber(t("accountPool.policy.token_budget_limit"), policy.routing.token_budget_limit, (next) =>
          updateRouting("token_budget_limit", next),
        )}
        {number(
          t("accountPool.policy.token_budget_window_seconds"),
          policy.routing.token_budget_window_seconds,
          (next) => updateRouting("token_budget_window_seconds", next),
        )}
        {number(t("accountPool.policy.max_attempts"), policy.routing.max_attempts, (next) =>
          updateRouting("max_attempts", next),
        )}
        {number(t("accountPool.policy.backoff_ms"), policy.routing.backoff_ms, (next) =>
          updateRouting("backoff_ms", next),
        )}
        {text(t("accountPool.policy.preferred_account_ids"), preferred, setPreferred)}
        {text(t("accountPool.policy.retryable_statuses"), statuses, setStatuses)}
        {text(t("accountPool.policy.excluded_models"), excluded, setExcluded)}
        {text(t("accountPool.policy.model_aliases"), aliases, setAliases)}
        {toggle(t("accountPool.policy.is_backup"), policy.routing.is_backup, (next) =>
          updateRouting("is_backup", next),
        )}
        {toggle(t("accountPool.policy.session_affinity"), policy.routing.session_affinity, (next) =>
          updateRouting("session_affinity", next),
        )}
        {toggle(t("accountPool.policy.fallback_enabled"), policy.routing.fallback_enabled, (next) =>
          updateRouting("fallback_enabled", next),
        )}
      </fieldset>
      <fieldset className="grid gap-3 rounded-md border p-4 sm:grid-cols-2">
        <legend className="mb-3 font-medium">{t("accountPool.policy.transport")}</legend>
        {select(
          t("accountPool.policy.image_generation"),
          policy.transport.image_generation,
          ["inherit", "enabled", "disabled"],
          (next) => updateTransport("image_generation", next as Transport["image_generation"]),
        )}
        {select(
          t("accountPool.policy.websocket"),
          policy.transport.websocket,
          ["inherit", "enabled", "disabled"],
          (next) => updateTransport("websocket", next as Transport["websocket"]),
        )}
        {number(t("accountPool.policy.request_timeout_seconds"), policy.transport.request_timeout_seconds, (next) =>
          updateTransport("request_timeout_seconds", next),
        )}
        {toggle(t("accountPool.policy.debug_log_enabled"), policy.transport.debug_log_enabled, (next) =>
          updateTransport("debug_log_enabled", next),
        )}
      </fieldset>
      <fieldset className="grid gap-3 rounded-md border p-4 sm:grid-cols-2">
        <legend className="mb-3 font-medium">{t("accountPool.policy.provider")}</legend>
        <AccountPoolProviderPolicyFields
          supplier={supplier}
          busy={busy}
          codex={codex}
          claude={claude}
          kimi={kimi}
          xai={xai}
          antigravity={antigravity}
          clients={clients}
          sensitiveWords={sensitiveWords}
          onClientsChange={setClients}
          onSensitiveWordsChange={setSensitiveWords}
          onCodexChange={updateCodex}
          onClaudeChange={updateClaude}
          onKimiChange={updateKimi}
          onXaiChange={updateXai}
          onAntigravityChange={updateAntigravity}
        />
      </fieldset>
      <Button type="submit" disabled={busy}>
        {t(busy ? "accountPool.config.saving" : "accountPool.config.save")}
      </Button>
    </form>
  );
}
