import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { discoverAccountPoolModelCatalog } from "@/features/account-pool/api/AccountPoolApi";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";

const changesBetween = (before: string[], after: string[]) => ({
  added: after.filter((model) => !before.includes(model)),
  removed: before.filter((model) => !after.includes(model)),
});

export function useRuntimeModelCatalog(accessToken: string | null, environment: AccountPoolEnvironment, open: boolean) {
  const isCodex = environment.channel === "cliproxyapi" && environment.supplier === "openai_codex";
  const lifecycleReady = ["ready", "cooling_down", "disabled", "error"].includes(environment.status);
  const supportsDiscovery = isCodex && lifecycleReady && !environment.configuration_pending;
  const [lastChanges, setLastChanges] = useState<ReturnType<typeof changesBetween>>();
  const options = {
    queryKey: accountPoolQueryKeys.modelCatalog(accessToken, environment.id, environment.version),
    queryFn: () => discoverAccountPoolModelCatalog(accessToken!, environment.id),
    enabled: open && accessToken !== null && supportsDiscovery,
    staleTime: Infinity,
    retry: false as const,
  };
  const catalog = useQuery(options);
  const models = catalog.data ?? environment.available_models;
  return {
    models,
    supportsDiscovery,
    loading: catalog.isFetching,
    error: catalog.isError,
    changes: lastChanges ?? (catalog.data ? changesBetween(environment.available_models, models) : undefined),
    discover: async () => {
      const result = await catalog.refetch();
      if (result.isSuccess) setLastChanges(changesBetween(models, result.data));
    },
  };
}

export function RuntimeModelChoices({
  catalog,
  selected,
  disabled,
  onChange,
}: {
  catalog: ReturnType<typeof useRuntimeModelCatalog>;
  selected: string[];
  disabled: boolean;
  onChange: (models: string[]) => void;
}) {
  const { t } = useTranslation();
  const { models, supportsDiscovery, loading, error, changes, discover } = catalog;
  let message: string | null = null;
  if (error) {
    message = t("accountPool.config.modelCatalogFailed");
  } else if (changes) {
    message = changes.added.length || changes.removed.length
      ? t("accountPool.config.modelCatalogChanges", {
        added: changes.added.join(", ") || "—", removed: changes.removed.join(", ") || "—",
      })
      : t("accountPool.config.modelCatalogUnchanged");
  }
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>{t("accountPool.config.enabledModels")}</Label>
        {supportsDiscovery && (
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" disabled={disabled || loading} onClick={() => void discover()}>
              {t(loading ? "accountPool.config.discoveringModels" : "accountPool.config.discoverModels")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || models.every((model) => selected.includes(model))}
              onClick={() => onChange([...new Set([...selected, ...models])])}
            >
              {t("accountPool.config.enableAllModels")}
            </Button>
          </div>
        )}
      </div>
      {supportsDiscovery && message && (
        <p className="text-xs text-muted-foreground" role="status">
          {message}
        </p>
      )}
      <div className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-2">
        {models.map((model) => (
          <label key={model} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={selected.includes(model)}
              disabled={disabled}
              onCheckedChange={(next) => onChange(next === true ? [...selected, model] : selected.filter((id) => id !== model))}
            />
            <span className="truncate">{model}</span>
          </label>
        ))}
        {models.length === 0 && <p className="text-sm text-muted-foreground">{t("accountPool.config.noAvailableModels")}</p>}
      </div>
    </div>
  );
}
