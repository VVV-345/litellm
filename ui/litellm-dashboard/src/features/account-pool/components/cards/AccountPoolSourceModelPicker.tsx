/** 本文件提供资源侧模型候选的常驻勾选面板，勾选结果通过回调交给父表单。 */

import { Search, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cva.config";

export const AccountPoolSourceModelPicker = ({
  models,
  selected,
  onChange,
  disabled = false,
}: {
  models: readonly string[];
  selected: readonly string[];
  onChange: (models: string[]) => void;
  disabled?: boolean;
}) => {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const keyword = query.trim().toLowerCase();
  const matches = models.filter((model) => model.toLowerCase().includes(keyword));
  const toggle = (model: string) => {
    if (disabled) return;
    onChange(selected.includes(model) ? selected.filter((item) => item !== model) : [...selected, model]);
  };
  const selectAll = () => onChange([...selected, ...matches.filter((model) => !selected.includes(model))]);
  const clearAll = () => onChange([]);

  return (
    <div className="grid gap-2">
      {selected.length > 0 && (
        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label={t("accountPool.providers.openaiCompatible.selectedModels")}
        >
          {selected.map((model) => (
            <span
              key={model}
              className="inline-flex max-w-full items-center gap-0.5 rounded-full border border-primary/30 bg-primary/5 py-0.5 pr-0.5 pl-2 text-xs text-foreground"
            >
              <span className="truncate font-mono">{model}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                className="size-4 rounded-full text-muted-foreground"
                aria-label={t("accountPool.providers.openaiCompatible.removeSelectedModel", { model })}
                disabled={disabled}
                onClick={() => toggle(model)}
              >
                <X className="size-3" />
              </Button>
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="h-8 pl-8"
          aria-label={t("accountPool.providers.openaiCompatible.searchSourceModels")}
          placeholder={t("accountPool.providers.openaiCompatible.searchSourceModels")}
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {t("accountPool.providers.openaiCompatible.selectionCount", {
            selected: selected.length,
            total: models.length,
          })}
        </p>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={disabled || matches.length === 0}
            onClick={selectAll}
          >
            {t("accountPool.providers.openaiCompatible.selectAllModels")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={disabled || selected.length === 0}
            onClick={clearAll}
          >
            {t("accountPool.providers.openaiCompatible.clearModels")}
          </Button>
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto rounded-lg border bg-background">
        {matches.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            {t("accountPool.providers.openaiCompatible.noMatchingModels")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {matches.map((model, index) => {
              const checkboxId = `account-pool-source-model-${index}`;
              const checked = selected.includes(model);
              return (
                <li key={model}>
                  <label
                    htmlFor={checkboxId}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 px-3 py-2 text-sm transition-colors hover:bg-muted/40",
                      checked && "bg-primary/5 ring-1 ring-primary/40 ring-inset",
                    )}
                  >
                    <Checkbox
                      id={checkboxId}
                      checked={checked}
                      disabled={disabled}
                      onCheckedChange={() => toggle(model)}
                    />
                    <span className="truncate font-mono text-[13px]">{model}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
};
