import React, { forwardRef, useImperativeHandle, useMemo } from "react";
import { CircleHelp } from "lucide-react";
import { useForm, type Resolver } from "react-hook-form";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { FieldGroup } from "@/components/ui/field";
import { FormField } from "@/components/shared/form/FormField";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { MCPTool, InputSchema, InputSchemaProperty } from "./types";

type ToolFormValues = Record<string, unknown>;

const isBlank = (value: unknown): boolean => value === undefined || value === null || value === "";

const jsonErrorFor = (t: TFunction, prop: InputSchemaProperty, value: unknown): string | null => {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    const isValidObject =
      prop.type === "object" && parsed !== null && typeof parsed === "object" && !Array.isArray(parsed);
    const isValidArray = prop.type === "array" && Array.isArray(parsed);
    if (isValidObject || isValidArray) return null;
    return prop.type === "object" ? t("ui.Please enter a JSON object") : t("ui.Please enter a JSON array");
  } catch {
    return t("ui.Invalid JSON");
  }
};

type FieldError = { type: string; message: string };

const collectErrors = (
  t: TFunction,
  actualSchema: InputSchema,
  requiredMessages: Readonly<Record<string, string>>,
  values: ToolFormValues,
): Record<string, FieldError> => {
  const entries = Object.entries(actualSchema.properties ?? {}).flatMap<[string, FieldError]>(([key, prop]) => {
    const value = values[key];
    const blank = isBlank(value);
    if (actualSchema.required?.includes(key) && blank) {
      return [
        [key, { type: "required", message: requiredMessages[key] ?? t("ui.Please enter {{field}}", { field: key }) }],
      ];
    }
    if (prop.type !== "object" && prop.type !== "array") return [];
    if (blank) return [];
    const message = jsonErrorFor(t, prop, value);
    return message === null ? [] : [[key, { type: "validate", message }]];
  });
  return Object.fromEntries(entries);
};

const buildResolver =
  (
    t: TFunction,
    actualSchema: InputSchema,
    requiredMessages: Readonly<Record<string, string>> = {},
  ): Resolver<ToolFormValues> =>
  (values) => {
    const errors = collectErrors(t, actualSchema, requiredMessages, values);
    return Object.keys(errors).length > 0 ? { values: {}, errors } : { values, errors: {} };
  };

const labelFor = (key: string, prop: InputSchemaProperty, required: boolean): React.ReactNode => (
  <span className="flex items-center">
    {key} {required && <span className="text-destructive">*</span>}
    {prop.description && (
      <Tooltip>
        <TooltipTrigger render={<CircleHelp className="ml-2 size-3.5 shrink-0 cursor-help text-muted-foreground" />} />
        <TooltipContent>{prop.description}</TooltipContent>
      </Tooltip>
    )}
  </span>
);

const isPlainObject = (value: unknown): value is Record<string, any> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function buildArrayItems(items?: InputSchemaProperty | InputSchemaProperty[]): any[] {
  if (!items) return [];
  if (Array.isArray(items)) {
    return items.map((item) => buildDefaultValue(item)).filter((value) => value !== undefined);
  }
  const itemDefault = buildDefaultValue(items);
  return itemDefault !== undefined ? [itemDefault] : [];
}

function buildDefaultValue(prop?: InputSchemaProperty, overrideDefault?: any): any {
  if (!prop) return undefined;
  const effectiveDefault = overrideDefault !== undefined ? overrideDefault : prop.default;

  if (prop.type === "object") {
    const base = isPlainObject(effectiveDefault) ? { ...effectiveDefault } : {};
    if (prop.properties) {
      Object.entries(prop.properties).forEach(([childKey, childProp]) => {
        base[childKey] = buildDefaultValue(childProp, base[childKey]);
      });
    }
    return base;
  }

  if (prop.type === "array") {
    if (Array.isArray(effectiveDefault)) {
      const itemSchema = prop.items;
      if (!itemSchema) return effectiveDefault;
      if (effectiveDefault.length === 0) {
        const sample = buildArrayItems(itemSchema);
        return sample.length ? sample : effectiveDefault;
      }
      if (Array.isArray(itemSchema)) {
        return effectiveDefault.map((value, index) => {
          const schema = itemSchema[index] ?? itemSchema[itemSchema.length - 1];
          return buildDefaultValue(schema, value);
        });
      }
      return effectiveDefault.map((value) => buildDefaultValue(itemSchema, value));
    }
    if (effectiveDefault !== undefined) return effectiveDefault;
    return buildArrayItems(prop.items);
  }

  if (effectiveDefault !== undefined) return effectiveDefault;
  switch (prop.type) {
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "string":
    default:
      return "";
  }
}

const getInitialValueForField = (prop: InputSchemaProperty): any => {
  const defaultValue = buildDefaultValue(prop);
  if (prop.type === "object" || prop.type === "array") {
    const fallback = prop.type === "array" ? [] : {};
    return JSON.stringify(defaultValue ?? fallback, null, 2);
  }
  return defaultValue;
};

function convertFormValues(
  values: Record<string, any>,
  actualSchema: InputSchema,
  schema: InputSchema,
): Record<string, any> {
  const convertedValues: Record<string, any> = {};
  const schemaToUse = actualSchema;

  Object.entries(values).forEach(([key, value]) => {
    const prop = schemaToUse.properties?.[key];
    if (prop && value !== null && value !== undefined && value !== "") {
      switch (prop.type) {
        case "boolean":
          convertedValues[key] = value === "true" || value === true;
          break;
        case "number":
        case "integer": {
          const numericValue = Number(value);
          convertedValues[key] = Number.isNaN(numericValue)
            ? value
            : prop.type === "integer"
              ? Math.trunc(numericValue)
              : numericValue;
          break;
        }
        case "object":
        case "array": {
          try {
            const parsed = typeof value === "string" ? JSON.parse(value) : value;
            const isValidObject =
              prop.type === "object" && parsed !== null && typeof parsed === "object" && !Array.isArray(parsed);
            const isValidArray = prop.type === "array" && Array.isArray(parsed);
            if ((prop.type === "object" && isValidObject) || (prop.type === "array" && isValidArray)) {
              convertedValues[key] = parsed;
            } else {
              convertedValues[key] = value;
            }
          } catch {
            convertedValues[key] = value;
          }
          break;
        }
        case "string":
          convertedValues[key] = String(value);
          break;
        default:
          convertedValues[key] = value;
      }
    } else if (value !== null && value !== undefined && value !== "") {
      convertedValues[key] = value;
    }
  });

  const isNestedParams = schema.properties?.params?.type === "object" && schema.properties.params.properties;

  return isNestedParams ? { params: convertedValues } : convertedValues;
}

export interface MCPToolArgumentsFormRef {
  getSubmitValues: () => Promise<Record<string, any>>;
}

interface MCPToolArgumentsFormProps {
  tool: MCPTool;
  className?: string;
}

const MCPToolArgumentsForm = forwardRef<MCPToolArgumentsFormRef, MCPToolArgumentsFormProps>(
  ({ tool, className }, ref) => {
    const { t } = useTranslation();
    const stringSchemaMessages = useMemo<Readonly<Record<string, string>>>(
      () => ({ input: t("ui.Please enter input for this tool") }),
      [t],
    );
    const booleanItems = useMemo(
      () => [
        { value: true, label: t("ui.True") },
        { value: false, label: t("ui.False") },
      ],
      [t],
    );
    const schema: InputSchema = useMemo(() => {
      if (typeof tool.inputSchema === "string") {
        return {
          type: "object",
          properties: {
            input: {
              type: "string",
              description: t("ui.Input for this tool"),
            },
          },
          required: ["input"],
        };
      }
      return tool.inputSchema as InputSchema;
    }, [t, tool.inputSchema]);

    const actualSchema: InputSchema = useMemo(() => {
      if (schema.properties?.params?.type === "object" && schema.properties.params.properties) {
        return {
          type: "object",
          properties: schema.properties.params.properties,
          required: schema.properties.params.required || [],
        };
      }
      return schema;
    }, [schema]);

    const defaultValues = useMemo<ToolFormValues>(
      () =>
        Object.fromEntries(
          Object.entries(actualSchema.properties ?? {}).map(([key, prop]) => [key, getInitialValueForField(prop)]),
        ),
      [actualSchema],
    );

    const isStringSchema = typeof tool.inputSchema === "string";
    const requiredMessages = isStringSchema ? stringSchemaMessages : {};
    const form = useForm<ToolFormValues>({
      defaultValues,
      resolver: buildResolver(t, actualSchema, requiredMessages),
    });
    const { reset } = form;

    useImperativeHandle(ref, () => ({
      getSubmitValues: async () => {
        const values = form.getValues();
        const errors = collectErrors(t, actualSchema, requiredMessages, values);
        if (Object.keys(errors).length > 0) {
          await form.trigger();
          return Promise.reject({
            errorFields: Object.entries(errors).map(([name, error]) => ({
              name: [name],
              errors: [error.message],
            })),
          });
        }
        return convertFormValues(values, actualSchema, schema);
      },
    }));

    React.useEffect(() => {
      reset(defaultValues);
    }, [reset, defaultValues, tool]);

    if (isStringSchema) {
      return (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void form.trigger();
          }}
          className={className}
        >
          <FieldGroup>
            <FormField
              control={form.control}
              name="input"
              label={
                <span>
                  {t("ui.Input")} <span className="text-destructive">*</span>
                </span>
              }
            >
              {(field) => (
                <Input {...field} value={field.value as string} placeholder={t("ui.Enter input for this tool")} />
              )}
            </FormField>
          </FieldGroup>
        </form>
      );
    }

    if (!actualSchema.properties) {
      return (
        <form onSubmit={(event) => event.preventDefault()} className={className}>
          <div className="py-4 text-center text-sm text-muted-foreground">
            {t("ui.No parameters required for this tool.")}
          </div>
        </form>
      );
    }

    return (
      <TooltipProvider>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void form.trigger();
          }}
          className={className}
        >
          <FieldGroup>
            {Object.entries(actualSchema.properties).map(([key, prop]) => {
              const required = actualSchema.required?.includes(key) ?? false;
              return (
                <FormField
                  key={`${tool.name}-${key}`}
                  control={form.control}
                  name={key}
                  label={labelFor(key, prop, required)}
                >
                  {(field) => {
                    if (prop.type === "string" && prop.enum) {
                      return (
                        <Select value={field.value ?? ""} onValueChange={field.onChange}>
                          <SelectTrigger
                            id={field.id}
                            onBlur={field.onBlur}
                            aria-invalid={field["aria-invalid"]}
                            className="w-full"
                          >
                            <SelectValue placeholder={t("ui.Select {{field}}", { field: key })} />
                          </SelectTrigger>
                          <SelectContent>
                            {!required && <SelectItem value="">{t("ui.Select {{field}}", { field: key })}</SelectItem>}
                            {prop.enum.map((v) => (
                              <SelectItem key={v} value={v}>
                                {v}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      );
                    }
                    if (prop.type === "boolean") {
                      return (
                        <Select items={booleanItems} value={field.value ?? ""} onValueChange={field.onChange}>
                          <SelectTrigger
                            id={field.id}
                            onBlur={field.onBlur}
                            aria-invalid={field["aria-invalid"]}
                            className="w-full"
                          >
                            <SelectValue placeholder={t("ui.Select {{field}}", { field: key })} />
                          </SelectTrigger>
                          <SelectContent>
                            {!required && <SelectItem value="">{t("ui.Select {{field}}", { field: key })}</SelectItem>}
                            <SelectItem value={true}>{t("ui.True")}</SelectItem>
                            <SelectItem value={false}>{t("ui.False")}</SelectItem>
                          </SelectContent>
                        </Select>
                      );
                    }
                    if (prop.type === "number" || prop.type === "integer") {
                      return (
                        <Input
                          {...field}
                          type="number"
                          step={prop.type === "integer" ? 1 : undefined}
                          value={field.value as number | string}
                          placeholder={prop.description || t("ui.Enter {{field}}", { field: key })}
                        />
                      );
                    }
                    if (prop.type === "object" || prop.type === "array") {
                      return (
                        <Textarea
                          {...field}
                          rows={prop.type === "object" ? 4 : 3}
                          value={field.value as string}
                          spellCheck={false}
                          className="font-mono"
                          placeholder={
                            prop.description ||
                            (prop.type === "object"
                              ? t("ui.Enter JSON object for {{field}}", { field: key })
                              : t("ui.Enter JSON array for {{field}}", { field: key }))
                          }
                        />
                      );
                    }
                    return (
                      <Input
                        {...field}
                        value={field.value as string}
                        placeholder={prop.description || t("ui.Enter {{field}}", { field: key })}
                      />
                    );
                  }}
                </FormField>
              );
            })}
          </FieldGroup>
        </form>
      </TooltipProvider>
    );
  },
);

MCPToolArgumentsForm.displayName = "MCPToolArgumentsForm";

export default MCPToolArgumentsForm;
