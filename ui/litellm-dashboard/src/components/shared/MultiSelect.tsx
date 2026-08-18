// 本文件提供通用多选框，并可按需把用户输入创建为新的自定义选项。
"use client";

import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
} from "@/components/ui/combobox";

export interface MultiSelectOption {
  label: string;
  value: string;
  description?: string;
}

interface MultiSelectProps {
  options: MultiSelectOption[];
  value?: string[];
  onValueChange: (value: string[]) => void;
  placeholder?: string;
  emptyText?: string;
  disabled?: boolean;
  loading?: boolean;
  allowCustomValues?: boolean;
  className?: string;
}

const matchesQuery = (option: MultiSelectOption, query: string): boolean => {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return true;
  const matchesLabel = option.label.toLowerCase().includes(normalizedQuery);
  const matchesValue = option.value.toLowerCase().includes(normalizedQuery);
  const matchesDescription = option.description?.toLowerCase().includes(normalizedQuery) ?? false;
  return matchesLabel || matchesValue || matchesDescription;
};

export function MultiSelect({
  options,
  value = [],
  onValueChange,
  placeholder,
  emptyText,
  disabled = false,
  loading = false,
  allowCustomValues = false,
  className,
}: MultiSelectProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const safeOptions = options.filter(
    (option): option is MultiSelectOption =>
      option != null && typeof option.value === "string" && option.value.length > 0,
  );
  const selectedOptions = value
    .filter((selectedValue): selectedValue is string => typeof selectedValue === "string" && selectedValue.length > 0)
    .map(
      (selectedValue) =>
        safeOptions.find((option) => option.value === selectedValue) ?? {
          label: selectedValue,
          value: selectedValue,
        },
    );
  const customOption = query.trim();
  const customOptionExists = [...safeOptions.map((option) => option.value), ...value].some(
    (optionValue) => optionValue.toLowerCase() === customOption.toLowerCase(),
  );
  const items =
    allowCustomValues && customOption && !customOptionExists
      ? [...safeOptions, { label: t("common.createOption", { option: customOption }), value: customOption }]
      : safeOptions;
  const resolvedPlaceholder = placeholder ?? t("common.selectOptions");
  const resolvedEmptyText = emptyText ?? t("common.noOptionsFound");
  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const canCreateCustomOption = allowCustomValues && Boolean(customOption) && !customOptionExists;
    if (event.key !== "Enter" || !canCreateCustomOption) return;
    event.preventDefault();
    onValueChange([...value, customOption]);
    setQuery("");
  };

  return (
    <Combobox
      multiple
      items={items}
      value={selectedOptions}
      onValueChange={(selected: MultiSelectOption[]) => {
        onValueChange(selected.map((option) => option.value));
        setQuery("");
      }}
      inputValue={query}
      onInputValueChange={setQuery}
      isItemEqualToValue={(option: MultiSelectOption, selected: MultiSelectOption) => option.value === selected.value}
      itemToStringLabel={(option: MultiSelectOption) => option.label}
      filter={matchesQuery}
      disabled={disabled || loading}
    >
      <ComboboxChips className={`min-h-8 py-1 text-sm ${className ?? ""}`}>
        <ComboboxValue>
          {(selected: MultiSelectOption[]) =>
            selected.map((option) => (
              <ComboboxChip key={option.value} aria-label={option.label}>
                {option.label}
              </ComboboxChip>
            ))
          }
        </ComboboxValue>
        <ComboboxChipsInput
          placeholder={loading ? t("common.loading") : resolvedPlaceholder}
          className="h-5 min-w-24 flex-1 border-0 bg-transparent py-0 text-sm"
          aria-label={resolvedPlaceholder}
          onKeyDown={handleKeyDown}
        />
      </ComboboxChips>
      <ComboboxContent>
        <ComboboxEmpty>{resolvedEmptyText}</ComboboxEmpty>
        <ComboboxList>
          {(option: MultiSelectOption) => (
            <ComboboxItem key={option.value} value={option}>
              <span className="min-w-0">
                <span className="block truncate">{option.label}</span>
                {option.description && (
                  <span className="block truncate text-xs text-muted-foreground">{option.description}</span>
                )}
              </span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
