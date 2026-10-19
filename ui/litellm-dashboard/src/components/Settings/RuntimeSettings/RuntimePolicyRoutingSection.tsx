"use client";

import { useTranslation } from "react-i18next";

import { MultiSelect } from "@/components/shared/MultiSelect";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { AccountPolicy } from "@/features/account-pool/api/AccountPoolManagementApi";
import type { AccountPoolPolicyOption } from "@/features/account-pool/utils/accountPoolPolicyOptions";

type Routing = NonNullable<AccountPolicy["routing"]>;
type RoutingChange = <K extends keyof Routing>(field: K, value: Routing[K]) => void;

const STRATEGIES = ["auto", "random", "priority", "quota", "plan", "expiry", "custom"] as const;

const FieldCard = ({ children }: { children: React.ReactNode }) => (
  <div className="min-w-0 rounded-lg border border-border bg-background p-4">{children}</div>
);

function FieldHeader({ label, description }: { label: string; description: string }) {
  return (
    <div className="space-y-1">
      <Label className="block break-words leading-5">{label}</Label>
      <p className="break-words text-xs leading-5 text-muted-foreground">{description}</p>
    </div>
  );
}

function ToggleField({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <FieldCard>
      <div className="flex min-w-0 items-start justify-between gap-4">
        <FieldHeader label={label} description={description} />
        <Switch aria-label={label} checked={checked} disabled={disabled} onCheckedChange={(next) => onChange(next === true)} />
      </div>
    </FieldCard>
  );
}

function NumberField({
  label,
  description,
  value,
  min,
  max,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  value: number;
  min?: number;
  max?: number;
  disabled: boolean;
  onChange: (next: number) => void;
}) {
  return (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <FieldHeader label={label} description={description} />
        <Input
          aria-label={label}
          type="number"
          value={value}
          min={min}
          max={max}
          step={1}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      </div>
    </FieldCard>
  );
}

function NumberListField({
  label,
  description,
  value,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  value: readonly number[];
  disabled: boolean;
  onChange: (next: number[]) => void;
}) {
  return (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <FieldHeader label={label} description={description} />
        <Input
          aria-label={label}
          value={value.join(", ")}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              event.target.value
                .split(",")
                .map((item) => Number(item.trim()))
                .filter((item) => Number.isInteger(item)),
            )
          }
        />
      </div>
    </FieldCard>
  );
}

function SelectField({
  label,
  description,
  value,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <FieldCard>
      <div className="grid min-w-0 gap-2">
        <FieldHeader label={label} description={description} />
        <Select value={value} disabled={disabled} onValueChange={(next) => next !== null && onChange(next)}>
          <SelectTrigger aria-label={label} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STRATEGIES.map((option) => (
              <SelectItem key={option} value={option}>
                {t(`accountPool.policy.options.${option}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </FieldCard>
  );
}

export function RuntimePolicyRoutingSection({
  routing,
  accounts,
  busy,
  onChange,
}: {
  routing: Routing;
  accounts: AccountPoolPolicyOption[];
  busy: boolean;
  onChange: RoutingChange;
}) {
  const { t } = useTranslation();
  const description = (field: string) => t(`accountPool.policy.descriptions.${field}`);

  return (
    <>
      <p className="text-sm leading-6 text-muted-foreground">{t("accountPool.policy.cardRoutingDescription")}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label={t("accountPool.policy.strategy")}
          description={description("strategy")}
          value={routing.strategy}
          disabled={busy}
          onChange={(next) => onChange("strategy", next as Routing["strategy"])}
        />
        <NumberField
          label={t("accountPool.policy.priority")}
          description={description("priority")}
          value={routing.priority}
          min={-10000}
          max={10000}
          disabled={busy}
          onChange={(next) => onChange("priority", next)}
        />
        <NumberField
          label={t("accountPool.policy.weight")}
          description={description("weight")}
          value={routing.weight}
          min={1}
          max={10000}
          disabled={busy}
          onChange={(next) => onChange("weight", next)}
        />
        <ToggleField
          label={t("accountPool.policy.is_backup")}
          description={description("is_backup")}
          checked={routing.is_backup}
          disabled={busy}
          onChange={(next) => onChange("is_backup", next)}
        />
        <FieldCard>
          <div className="grid min-w-0 gap-2">
            <FieldHeader
              label={t("accountPool.policy.preferred_account_ids")}
              description={description("preferred_account_ids")}
            />
            <MultiSelect
              value={routing.preferred_account_ids}
              options={accounts}
              onValueChange={(next) => onChange("preferred_account_ids", next)}
              disabled={busy}
              placeholder={t("accountPool.policy.selectOptions")}
              emptyText={t("accountPool.policy.noMatchingOptions")}
            />
          </div>
        </FieldCard>
        <ToggleField
          label={t("accountPool.policy.session_affinity")}
          description={description("session_affinity")}
          checked={routing.session_affinity}
          disabled={busy}
          onChange={(next) => onChange("session_affinity", next)}
        />
        <NumberField
          label={t("accountPool.policy.session_affinity_ttl")}
          description={description("session_affinity_ttl")}
          value={routing.session_affinity_ttl}
          min={60}
          max={86400}
          disabled={busy}
          onChange={(next) => onChange("session_affinity_ttl", next)}
        />
      </div>
      <h4 className="font-medium">{t("accountPool.policy.failoverAndRetry")}</h4>
      <div className="grid gap-3 sm:grid-cols-2">
        <ToggleField
          label={t("accountPool.policy.fallback_enabled")}
          description={description("fallback_enabled")}
          checked={routing.fallback_enabled}
          disabled={busy}
          onChange={(next) => onChange("fallback_enabled", next)}
        />
        <NumberField
          label={t("accountPool.policy.max_attempts")}
          description={description("max_attempts")}
          value={routing.max_attempts}
          min={1}
          max={5}
          disabled={busy}
          onChange={(next) => onChange("max_attempts", next)}
        />
        <NumberField
          label={t("accountPool.policy.backoff_ms")}
          description={description("backoff_ms")}
          value={routing.backoff_ms}
          min={0}
          max={60000}
          disabled={busy}
          onChange={(next) => onChange("backoff_ms", next)}
        />
        <NumberListField
          label={t("accountPool.policy.retryable_statuses")}
          description={description("retryable_statuses")}
          value={routing.retryable_statuses}
          disabled={busy}
          onChange={(next) => onChange("retryable_statuses", next)}
        />
      </div>
    </>
  );
}
