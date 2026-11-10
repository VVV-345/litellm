import React from "react";
import { useForm, type Control } from "react-hook-form";
import { CircleHelp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { FieldGroup } from "@/components/ui/field";
import { FormField, type FormFieldControlProps } from "@/components/shared/form/FormField";
import { UiLoadingSpinner } from "@/components/ui/ui-loading-spinner";
import type { InputSchemaProperty } from "@/components/mcp_tools/types";
import {
  ToolArgumentField,
  ToolArgumentsFormValues,
  buildToolCallArguments,
  initialArgumentValues,
  resolveSchemaProperty,
  toolArgumentsResolver,
} from "./toolCallArguments";
import { useTranslation } from "react-i18next";

const argumentLabel = (field: ToolArgumentField): React.ReactNode => (
  <span className="flex items-center">
    {field.key}
    {field.required && <span className="ml-1 text-destructive">*</span>}
    {field.prop.description && (
      <Tooltip>
        <TooltipTrigger render={<CircleHelp className="ml-2 size-3.5 shrink-0 cursor-help text-muted-foreground" />} />
        <TooltipContent>{field.prop.description}</TooltipContent>
      </Tooltip>
    )}
  </span>
);

const JsonArgumentControl: React.FC<{
  field: ToolArgumentField;
  prop: InputSchemaProperty;
  control: FormFieldControlProps<ToolArgumentsFormValues, `args.${number}`>;
}> = ({ field, prop, control }) => {
  const { t } = useTranslation();
  const isObject = prop.type === "object";
  const fallbackPlaceholder = isObject
    ? t("ui.Enter JSON object for {{field}}", { field: field.key })
    : t("ui.Enter JSON array for {{field}}", { field: field.key });
  return (
    <div className="space-y-2">
      <Textarea
        {...control}
        rows={isObject ? 6 : 4}
        value={(control.value as string) ?? ""}
        placeholder={prop.description || fallbackPlaceholder}
        spellCheck={false}
        data-testid={`textarea-${field.key}`}
        className="rounded-lg font-mono"
      />
      <p className="text-xs text-muted-foreground">
        {isObject ? t("ui.Provide a valid JSON object.") : t("ui.Provide a valid JSON array.")}
      </p>
    </div>
  );
};

const ToolArgumentControl: React.FC<{
  field: ToolArgumentField;
  control: FormFieldControlProps<ToolArgumentsFormValues, `args.${number}`>;
}> = ({ field, control }) => {
  const { t } = useTranslation();
  const prop = resolveSchemaProperty(field.prop);
  const selectPlaceholder = t("ui.Select {{field}}", { field: field.key });
  const booleanItems = [
    { value: true, label: t("ui.True") },
    { value: false, label: t("ui.False") },
  ];

  if (prop.type === "string" && prop.enum) {
    return (
      <select
        {...control}
        value={(control.value as string) ?? ""}
        className="w-full rounded-lg border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors focus:border-ring focus:ring-3 focus:ring-ring/50 focus:outline-hidden"
      >
        {!field.required && <option value="">{selectPlaceholder}</option>}
        {prop.enum.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (prop.type === "number" || prop.type === "integer") {
    return (
      <Input
        {...control}
        type="number"
        step={prop.type === "integer" ? 1 : "any"}
        value={(control.value as number | string) ?? ""}
        placeholder={prop.description || t("ui.Enter {{field}}", { field: field.key })}
        className="rounded-lg"
      />
    );
  }

  if (prop.type === "boolean") {
    return (
      <Select
        items={field.required ? booleanItems : [{ value: "", label: selectPlaceholder }, ...booleanItems]}
        value={control.value ?? ""}
        onValueChange={control.onChange}
      >
        <SelectTrigger
          id={control.id}
          aria-invalid={control["aria-invalid"]}
          title={booleanItems.find((item) => item.value === control.value)?.label}
          className="w-full"
        >
          <SelectValue placeholder={selectPlaceholder} />
        </SelectTrigger>
        <SelectContent>
          {!field.required && <SelectItem value="">{selectPlaceholder}</SelectItem>}
          <SelectItem value={true}>{t("ui.True")}</SelectItem>
          <SelectItem value={false}>{t("ui.False")}</SelectItem>
        </SelectContent>
      </Select>
    );
  }

  if (prop.type === "object" || prop.type === "array") {
    return <JsonArgumentControl field={field} prop={prop} control={control} />;
  }

  return (
    <Input
      {...control}
      value={(control.value as string) ?? ""}
      placeholder={prop.description || t("ui.Enter {{field}}", { field: field.key })}
      className="rounded-lg"
    />
  );
};

const ToolArgumentFields: React.FC<{
  fields: readonly ToolArgumentField[];
  control: Control<ToolArgumentsFormValues>;
  singleInputFallback: boolean;
}> = ({ fields, control, singleInputFallback }) => {
  const { t } = useTranslation();
  if (singleInputFallback) {
    return (
      <FieldGroup>
        <FormField
          control={control}
          name="args.0"
          label={
            <span>
              {t("ui.Input")} <span className="text-destructive">*</span>
            </span>
          }
        >
          {(field) => (
            <Input
              {...field}
              value={(field.value as string) ?? ""}
              placeholder={t("ui.Enter input for this tool")}
              className="rounded-lg"
            />
          )}
        </FormField>
      </FieldGroup>
    );
  }

  if (fields.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted py-6 text-center">
        <div className="mx-auto max-w-sm">
          <h4 className="mb-1 text-sm font-medium text-foreground">{t("ui.No Parameters Required")}</h4>
          <p className="text-xs text-muted-foreground">
            {t("ui.This tool can be called without any input parameters.")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <FieldGroup>
      {fields.map((field, index) => (
        <FormField
          key={`${field.key}-${index}`}
          control={control}
          name={`args.${index}` as const}
          label={argumentLabel(field)}
        >
          {(itemControl) => <ToolArgumentControl field={field} control={itemControl} />}
        </FormField>
      ))}
    </FieldGroup>
  );
};

export const ToolArgumentsForm: React.FC<{
  fields: readonly ToolArgumentField[];
  singleInputFallback: boolean;
  isLoading: boolean;
  hasRun: boolean;
  onRun: (args: Record<string, unknown>) => void;
}> = ({ fields, singleInputFallback, isLoading, hasRun, onRun }) => {
  const { t } = useTranslation();
  const form = useForm<ToolArgumentsFormValues>({
    defaultValues: { args: initialArgumentValues(fields) },
    resolver: toolArgumentsResolver(fields),
  });

  const submit = form.handleSubmit((values) => onRun(buildToolCallArguments(fields, values.args)));
  const idleLabel = hasRun ? t("ui.Call Again") : t("ui.Call Tool");

  return (
    <TooltipProvider>
      <form onSubmit={submit} className="space-y-3">
        <ToolArgumentFields fields={fields} control={form.control} singleInputFallback={singleInputFallback} />

        <div className="border-t border-border pt-3">
          <Button
            type="button"
            onClick={() => void submit()}
            disabled={isLoading}
            aria-busy={isLoading}
            className="w-full"
          >
            {isLoading && <UiLoadingSpinner className="size-4" />}
            {isLoading ? t("ui.Calling Tool...") : idleLabel}
          </Button>
        </div>
      </form>
    </TooltipProvider>
  );
};
