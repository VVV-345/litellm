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

type Routing = NonNullable<AccountPolicy["routing"]>;
type Transport = NonNullable<AccountPolicy["transport"]>;
type Codex = NonNullable<AccountPolicy["codex"]>;
type FormPolicy = Omit<AccountPolicy, "routing" | "transport" | "codex"> & {
  routing: Routing;
  transport: Transport;
  codex: Codex | null;
};

const codexDefaults: Codex = {
  identity_fingerprint_mode: "off",
  cli_only: false,
  allow_app_server: false,
  allow_app_server_clients: [],
  responses_compact_enabled: false,
  compact_ui: "inherit",
  experimental_context_management: false,
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
  const [clients, setClients] = useState(codex.allow_app_server_clients.join(", "));
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
      const { codex, ...rest } = parsed;
      await save({ ...rest, ...(supplier === "openai_codex" && codex ? { codex } : {}) });
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
        {supplier !== "openai_codex" ? (
          <p className="text-sm text-muted-foreground sm:col-span-2">{t("accountPool.policy.providerPending")}</p>
        ) : (
          <>
            {select(
              t("accountPool.policy.identity_fingerprint_mode"),
              codex.identity_fingerprint_mode,
              ["off", "device", "session", "full"],
              (next) => updateCodex("identity_fingerprint_mode", next as Codex["identity_fingerprint_mode"]),
            )}
            {select(t("accountPool.policy.compact_ui"), codex.compact_ui, ["inherit", "enabled", "disabled"], (next) =>
              updateCodex("compact_ui", next as Codex["compact_ui"]),
            )}
            {optionalNumber(t("accountPool.policy.model_context_window"), codex.model_context_window, (next) =>
              updateCodex("model_context_window", next),
            )}
            {optionalNumber(
              t("accountPool.policy.model_auto_compact_token_limit"),
              codex.model_auto_compact_token_limit,
              (next) => updateCodex("model_auto_compact_token_limit", next),
            )}
            {text(t("accountPool.policy.allow_app_server_clients"), clients, setClients)}
            {toggle(t("accountPool.policy.cli_only"), codex.cli_only, (next) => updateCodex("cli_only", next))}
            {toggle(t("accountPool.policy.allow_app_server"), codex.allow_app_server, (next) =>
              updateCodex("allow_app_server", next),
            )}
            {toggle(t("accountPool.policy.responses_compact_enabled"), codex.responses_compact_enabled, (next) =>
              updateCodex("responses_compact_enabled", next),
            )}
            {toggle(
              t("accountPool.policy.experimental_context_management"),
              codex.experimental_context_management,
              (next) => updateCodex("experimental_context_management", next),
            )}
          </>
        )}
      </fieldset>
      <Button type="submit" disabled={busy}>
        {t(busy ? "accountPool.config.saving" : "accountPool.config.save")}
      </Button>
    </form>
  );
}
