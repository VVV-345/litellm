import { Button, InputNumber, Select } from "antd";
import type { TFunction } from "i18next";
import React from "react";
import { useTranslation } from "react-i18next";

export interface BudgetWindowEntry {
  budget_duration: string;
  max_budget: number | null;
}

export const BUDGET_WINDOW_OPTIONS = (t: TFunction) => [
  { value: "1h", label: t("ui.Hourly"), resetHint: t("ui.Resets every hour") },
  { value: "24h", label: t("ui.Daily"), resetHint: t("ui.Resets daily at midnight UTC") },
  { value: "7d", label: t("ui.Weekly"), resetHint: t("ui.Resets every Sunday at midnight UTC") },
  { value: "30d", label: t("ui.Monthly"), resetHint: t("ui.Resets on the 1st of every month at midnight UTC") },
];

interface BudgetWindowsEditorProps {
  value: BudgetWindowEntry[];
  onChange: (v: BudgetWindowEntry[]) => void;
}

export function BudgetWindowsEditor({ value, onChange }: BudgetWindowsEditorProps) {
  const { t } = useTranslation();
  const addWindow = () => {
    onChange([...value, { budget_duration: "24h", max_budget: null }]);
  };

  const removeWindow = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const updateWindow = (idx: number, field: keyof BudgetWindowEntry, fieldValue: string | number | null) => {
    const updated = value.map((w, i) => (i === idx ? { ...w, [field]: fieldValue } : w));
    onChange(updated);
  };

  return (
    <div>
      {value.map((window, idx) => {
        const hint = BUDGET_WINDOW_OPTIONS(t).find((o) => o.value === window.budget_duration)?.resetHint;
        return (
          <div key={idx} style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Select
                value={window.budget_duration}
                onChange={(v) => updateWindow(idx, "budget_duration", v)}
                style={{ width: 130 }}
                options={BUDGET_WINDOW_OPTIONS(t).map((o) => ({ value: o.value, label: o.label }))}
              />
              <InputNumber
                step={0.01}
                min={0}
                precision={2}
                value={window.max_budget ?? undefined}
                onChange={(v) => updateWindow(idx, "max_budget", v ?? null)}
                placeholder={t("ui.Max spend ($)")}
                style={{ width: 160 }}
                prefix="$"
              />
              <Button type="text" danger size="small" onClick={() => removeWindow(idx)} style={{ padding: "0 4px" }}>
                ✕
              </Button>
            </div>
            {hint && <div style={{ fontSize: 11, color: "#888", marginTop: 3, marginLeft: 2 }}>↻ {hint}</div>}
          </div>
        );
      })}
      <Button
        size="small"
        onClick={(e) => {
          e.preventDefault();
          addWindow();
        }}
      >
        + {t("ui.Add Budget Window")}
      </Button>
    </div>
  );
}
