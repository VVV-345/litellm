/** 编辑单张号池卡片的路由、传输和供应商策略。 */

import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { MultiSelect, type MultiSelectOption } from "@/components/shared/MultiSelect";
import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/lib/toast";

import { AccountPoolProviderPolicyFields } from "./AccountPoolProviderPolicyFields";
import { getAccountPolicy, saveAccountPolicy, type AccountPolicy, type PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { buildAccountPoolPolicyOptions, type AccountPoolPolicyOptions } from "./accountPoolPolicyOptions";

type Routing = NonNullable<AccountPolicy["routing"]>;
type Transport = NonNullable<AccountPolicy["transport"]>;
type Codex = NonNullable<AccountPolicy["codex"]>;
type Claude = NonNullable<AccountPolicy["claude"]>;
type Kimi = NonNullable<AccountPolicy["kimi"]>;
type Xai = NonNullable<AccountPolicy["xai"]>;
type Antigravity = NonNullable<AccountPolicy["antigravity"]>;

interface MultiFieldProps {
  label: string;
  selected: string[];
  entries: MultiSelectOption[];
  change: (next: string[]) => void;
  allowCustomValues?: boolean;
}
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
  identity_fingerprint_mode: "off",
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

const FieldCard = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`min-w-0 rounded-lg border border-border bg-background p-4 ${className}`}>{children}</div>
);

export function AccountPoolPolicyDialog({
  accessToken,
  environment,
  environments,
  policies,
  onOpenRuntimeConfig,
  onClose,
}: {
  accessToken: string;
  environment: AccountPoolEnvironment;
  environments: readonly AccountPoolEnvironment[];
  policies: readonly PolicyView[];
  onOpenRuntimeConfig: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryOptions = {
    queryKey: ["account-pool", "policy", accessToken, environment.id],
    queryFn: () => getAccountPolicy(accessToken, environment.id),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  };
  const query = useQuery(queryOptions);
  const options = buildAccountPoolPolicyOptions(environment, environments, policies);
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-hidden p-0 sm:max-w-5xl">
        <DialogHeader className="border-b border-border px-6 pt-6 pb-4">
          <DialogTitle>{t("accountPool.policy.title", { name: environment.name })}</DialogTitle>
          <DialogDescription>{t("accountPool.policy.description")}</DialogDescription>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3 text-sm">
            <p className="min-w-0 flex-1 leading-6 text-muted-foreground">
              {t("accountPool.policy.cardScopedDescription")}
            </p>
            <Button type="button" variant="outline" size="sm" onClick={onOpenRuntimeConfig}>
              {t("accountPool.policy.openRuntimeConfig")}
            </Button>
          </div>
        </DialogHeader>
        <div className="max-h-[calc(100dvh-14rem)] overflow-y-auto px-6 pb-6">
          {query.isPending && <p className="py-6">{t("accountPool.management.loading")}</p>}
          {query.isError && (
            <div className="grid gap-3 py-6" role="alert">
              <p>{t("accountPool.policy.loadFailed")}</p>
              <Button className="w-fit" onClick={() => void query.refetch()}>
                {t("accountPool.retry")}
              </Button>
            </div>
          )}
          {query.data && (
            <PolicyForm
              key={query.data.version}
              value={query.data}
              environment={environment}
              options={options}
              save={async (policy) => {
                await saveAccountPolicy(accessToken, environment.id, query.data.version, policy);
                await query.refetch();
              }}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function PolicyForm({
  value,
  environment,
  options,
  save,
}: {
  value: PolicyView;
  environment: AccountPoolEnvironment;
  options: AccountPoolPolicyOptions;
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
  const [aliases, setAliases] = useState(policy.model_aliases.map((item) => `${item.alias}=${item.target}`).join(", "));
  const [statuses, setStatuses] = useState(policy.routing.retryable_statuses.join(", "));
  const codex = policy.codex ?? codexDefaults;
  const claude = policy.claude ?? claudeDefaults;
  const kimi = policy.kimi ?? kimiDefaults;
  const xai = policy.xai ?? xaiDefaults;
  const antigravity = policy.antigravity ?? antigravityDefaults;
  const groupOptions = options.groups.map((value) => ({
    label: value,
    value,
  }));
  const selectedGroup = groupOptions.find((option) => option.value === policy.group) ?? null;
  const preferredAccountOptions = [
    { label: environment.name, value: environment.id, description: environment.id },
    ...options.accounts.filter((option) => policy.account_ids?.includes(option.value)),
  ];
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
  const textField = (label: string, current: string, change: (next: string) => void) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <Label className="break-words leading-5">{label}</Label>
        <Input aria-label={label} value={current} disabled={busy} onChange={(event) => change(event.target.value)} />
      </div>
    </FieldCard>
  );
  const toggleField = (label: string, checked: boolean, change: (next: boolean) => void) => (
    <FieldCard>
      <div className="flex min-w-0 items-center justify-between gap-4">
        <Label className="min-w-0 break-words leading-5">{label}</Label>
        <Switch
          aria-label={label}
          checked={checked}
          disabled={busy}
          onCheckedChange={(next) => change(next === true)}
        />
      </div>
    </FieldCard>
  );
  const numberField = (label: string, current: number, change: (next: number) => void) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <Label className="break-words leading-5">{label}</Label>
        <Input
          aria-label={label}
          type="number"
          value={current}
          disabled={busy}
          onChange={(event) => change(Number(event.target.value))}
        />
      </div>
    </FieldCard>
  );
  const optionalNumberField = (
    label: string,
    current: number | null | undefined,
    change: (next: number | null) => void,
  ) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <Label className="break-words leading-5">{label}</Label>
        <Input
          aria-label={label}
          type="number"
          value={current ?? ""}
          disabled={busy}
          onChange={(event) => change(event.target.value ? Number(event.target.value) : null)}
        />
      </div>
    </FieldCard>
  );
  const selectField = (label: string, current: string, entries: readonly string[], change: (next: string) => void) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <Label className="break-words leading-5">{label}</Label>
        <Select value={current} disabled={busy} onValueChange={(next) => next !== null && change(next)}>
          <SelectTrigger aria-label={label} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {entries.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`accountPool.policy.options.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </FieldCard>
  );
  const multiField = ({ label, selected, entries, change, allowCustomValues = false }: MultiFieldProps) => (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <Label className="break-words leading-5">{label}</Label>
        <MultiSelect
          value={selected}
          options={entries}
          onValueChange={change}
          allowCustomValues={allowCustomValues}
          disabled={busy}
          placeholder={t(allowCustomValues ? "accountPool.policy.selectOrCreate" : "accountPool.policy.selectOptions")}
          emptyText={t("accountPool.policy.noMatchingOptions")}
        />
      </div>
    </FieldCard>
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
      model_aliases: aliasValues.map((item) => {
        const split = item.indexOf("=");
        return { alias: item.slice(0, split).trim(), target: item.slice(split + 1).trim() };
      }),
      routing: {
        ...policy.routing,
        retryable_statuses: list(statuses).map(Number),
      },
    };
    setBusy(true);
    try {
      const {
        codex: parsedCodex,
        claude: parsedClaude,
        kimi: parsedKimi,
        xai: parsedXai,
        antigravity: parsedAntigravity,
        ...rest
      } = parsed;
      const nextPolicy: AccountPolicy = {
        ...rest,
        ...(environment.supplier === "openai_codex" && parsedCodex ? { codex: parsedCodex } : {}),
        ...(environment.supplier === "anthropic_claude" && parsedClaude ? { claude: parsedClaude } : {}),
        ...(environment.supplier === "kimi" && parsedKimi ? { kimi: parsedKimi } : {}),
        ...(environment.supplier === "xai" && parsedXai ? { xai: parsedXai } : {}),
        ...(environment.supplier === "google_antigravity" && parsedAntigravity
          ? { antigravity: parsedAntigravity }
          : {}),
      };
      await save(nextPolicy);
      toast.success(t("accountPool.policy.saved"));
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="grid gap-6 pt-5" onSubmit={handleSubmit}>
      <div className="grid gap-3 sm:grid-cols-2">
        {multiField({
          label: t("accountPool.policy.tags"),
          selected: policy.tags,
          entries: options.tags,
          change: (tags) => setPolicy((current) => ({ ...current, tags })),
          allowCustomValues: true,
        })}
        <FieldCard>
          <div className="grid min-w-0 gap-2">
            <Label className="break-words leading-5">{t("accountPool.policy.group")}</Label>
            <Combobox
              items={groupOptions}
              value={selectedGroup}
              onValueChange={(group: MultiSelectOption | null) =>
                setPolicy((current) => ({ ...current, group: group?.value ?? "" }))
              }
              inputValue={policy.group}
              onInputValueChange={(group) => setPolicy((current) => ({ ...current, group }))}
              isItemEqualToValue={(option: MultiSelectOption, current: MultiSelectOption) =>
                option.value === current.value
              }
              itemToStringLabel={(option: MultiSelectOption) => option.label}
              filter={(option: MultiSelectOption, query: string) =>
                option.label.toLowerCase().includes(query.trim().toLowerCase())
              }
              openOnInputClick
            >
              <ComboboxInput
                aria-label={t("accountPool.policy.group")}
                placeholder={t("accountPool.policy.selectOrCreateGroup")}
                className="w-full"
                showClear
              />
              <ComboboxContent>
                <ComboboxEmpty>{t("accountPool.policy.createCurrentGroup", { group: policy.group })}</ComboboxEmpty>
                <ComboboxList>
                  {(group: MultiSelectOption) => (
                    <ComboboxItem key={group.value} value={group}>
                      {group.label}
                    </ComboboxItem>
                  )}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          </div>
        </FieldCard>
      </div>
      <section className="grid gap-3">
        <div className="space-y-1">
          <h3 className="font-medium">{t("accountPool.policy.capabilityTitle")}</h3>
          <p className="text-sm leading-6 text-muted-foreground">{t("accountPool.policy.pending")}</p>
        </div>
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {value.capabilities?.map((capability) => (
            <div key={capability.name} className="min-w-0 rounded-lg border border-border p-3">
              <dt className="break-words font-medium leading-5">
                {t(`accountPool.policy.capabilities.${capability.name}`)}
              </dt>
              <dd className="mt-2 break-words text-xs leading-5 text-muted-foreground">
                {t(`accountPool.policy.capabilityStatus.${capability.status}`)}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="grid gap-3">
        <div className="space-y-1">
          <h3 className="font-medium">{t("accountPool.policy.accountScope")}</h3>
          <p className="text-sm leading-6 text-muted-foreground">{t("accountPool.policy.accountScopeDescription")}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {multiField({
            label: t("accountPool.policy.account_ids"),
            selected: policy.account_ids ?? [],
            entries: options.accounts,
            change: (accountIds) =>
              setPolicy((current) => ({
                ...current,
                account_ids: accountIds,
                routing: {
                  ...current.routing,
                  preferred_account_ids: current.routing.preferred_account_ids.filter(
                    (accountId) => accountId === environment.id || accountIds.includes(accountId),
                  ),
                },
              })),
          })}
          {multiField({
            label: t("accountPool.policy.preferred_account_ids"),
            selected: policy.routing.preferred_account_ids,
            entries: preferredAccountOptions,
            change: (preferredAccountIds) => updateRouting("preferred_account_ids", preferredAccountIds),
          })}
        </div>
      </section>
      <section className="grid gap-3">
        <h3 className="font-medium">{t("accountPool.policy.routing")}</h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {selectField(
            t("accountPool.policy.strategy"),
            policy.routing.strategy,
            ["auto", "random", "priority", "quota", "plan", "expiry", "custom"],
            (next) => updateRouting("strategy", next as Routing["strategy"]),
          )}
          {numberField(t("accountPool.policy.priority"), policy.routing.priority, (next) =>
            updateRouting("priority", next),
          )}
          {numberField(t("accountPool.policy.weight"), policy.routing.weight, (next) => updateRouting("weight", next))}
          {numberField(t("accountPool.policy.session_affinity_ttl"), policy.routing.session_affinity_ttl, (next) =>
            updateRouting("session_affinity_ttl", next),
          )}
          {numberField(t("accountPool.policy.quota_reserve_percent"), policy.routing.quota_reserve_percent, (next) =>
            updateRouting("quota_reserve_percent", next),
          )}
          {numberField(t("accountPool.policy.quota_snapshot_max_age"), policy.routing.quota_snapshot_max_age, (next) =>
            updateRouting("quota_snapshot_max_age", next),
          )}
          {optionalNumberField(t("accountPool.policy.token_budget_limit"), policy.routing.token_budget_limit, (next) =>
            updateRouting("token_budget_limit", next),
          )}
          {numberField(
            t("accountPool.policy.token_budget_window_seconds"),
            policy.routing.token_budget_window_seconds,
            (next) => updateRouting("token_budget_window_seconds", next),
          )}
          {numberField(t("accountPool.policy.max_attempts"), policy.routing.max_attempts, (next) =>
            updateRouting("max_attempts", next),
          )}
          {numberField(t("accountPool.policy.backoff_ms"), policy.routing.backoff_ms, (next) =>
            updateRouting("backoff_ms", next),
          )}
          {textField(t("accountPool.policy.retryable_statuses"), statuses, setStatuses)}
          {multiField({
            label: t("accountPool.policy.excluded_models"),
            selected: policy.excluded_models,
            entries: options.models,
            change: (models) => setPolicy((current) => ({ ...current, excluded_models: models })),
            allowCustomValues: true,
          })}
          {textField(t("accountPool.policy.model_aliases"), aliases, setAliases)}
          {toggleField(t("accountPool.policy.is_backup"), policy.routing.is_backup, (next) =>
            updateRouting("is_backup", next),
          )}
          {toggleField(t("accountPool.policy.session_affinity"), policy.routing.session_affinity, (next) =>
            updateRouting("session_affinity", next),
          )}
          {toggleField(t("accountPool.policy.fallback_enabled"), policy.routing.fallback_enabled, (next) =>
            updateRouting("fallback_enabled", next),
          )}
        </div>
      </section>
      <section className="grid gap-3">
        <h3 className="font-medium">{t("accountPool.policy.transport")}</h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {selectField(
            t("accountPool.policy.image_generation"),
            policy.transport.image_generation,
            ["inherit", "enabled", "disabled"],
            (next) => updateTransport("image_generation", next as Transport["image_generation"]),
          )}
          {selectField(
            t("accountPool.policy.websocket"),
            policy.transport.websocket,
            ["inherit", "enabled", "disabled"],
            (next) => updateTransport("websocket", next as Transport["websocket"]),
          )}
          {numberField(
            t("accountPool.policy.request_timeout_seconds"),
            policy.transport.request_timeout_seconds,
            (next) => updateTransport("request_timeout_seconds", next),
          )}
          {toggleField(t("accountPool.policy.debug_log_enabled"), policy.transport.debug_log_enabled, (next) =>
            updateTransport("debug_log_enabled", next),
          )}
        </div>
      </section>
      <section className="grid gap-3">
        <div className="space-y-1">
          <h3 className="font-medium">{t("accountPool.policy.provider")}</h3>
          <p className="text-sm leading-6 text-muted-foreground">{t("accountPool.policy.providerDescription")}</p>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <AccountPoolProviderPolicyFields
            environment={environment}
            busy={busy}
            codex={codex}
            claude={claude}
            kimi={kimi}
            xai={xai}
            antigravity={antigravity}
            codexClientOptions={options.codexAppServerClients}
            sensitiveWordOptions={options.antigravitySensitiveWords}
            onCodexChange={updateCodex}
            onClaudeChange={updateClaude}
            onKimiChange={updateKimi}
            onXaiChange={updateXai}
            onAntigravityChange={updateAntigravity}
          />
        </div>
      </section>
      <div className="sticky bottom-0 z-sticky -mx-6 flex items-center justify-between gap-3 border-t border-border bg-popover/95 px-6 py-4 backdrop-blur">
        <p className="text-xs text-muted-foreground">{t("accountPool.policy.version", { version: value.version })}</p>
        <Button type="submit" disabled={busy}>
          {t(busy ? "accountPool.config.saving" : "accountPool.config.save")}
        </Button>
      </div>
    </form>
  );
}
