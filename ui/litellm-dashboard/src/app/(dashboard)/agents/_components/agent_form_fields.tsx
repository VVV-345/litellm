import React from "react";
import { useTranslation } from "react-i18next";
import { useFieldArray, useFormContext } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Field, FieldGroup, FieldTitle } from "@/components/ui/field";
import { AGENT_FORM_CONFIG, getAgentFormConfig, getSkillFieldConfig, SKILL_FIELD_CONFIG } from "./agent_config";
import CostConfigFields, { COST_FIELD_NAMES } from "./cost_config_fields";
import {
  AgentFormField,
  AgentFormPanel,
  AgentFormValues,
  AgentTagsInput,
  CollapsiblePanelsState,
  labelWithHint,
} from "./AgentFormKit";

const AUTH_HEADERS_PANEL_KEY = "auth_headers";

const namesOf = (fields: readonly { name: string }[]): readonly string[] => fields.map((field) => field.name);

export const A2A_PANEL_FIELD_NAMES: Readonly<Record<string, readonly string[]>> = {
  [AGENT_FORM_CONFIG.basic.key]: namesOf(AGENT_FORM_CONFIG.basic.fields),
  [AGENT_FORM_CONFIG.skills.key]: ["skills"],
  [AGENT_FORM_CONFIG.capabilities.key]: namesOf(AGENT_FORM_CONFIG.capabilities.fields),
  [AGENT_FORM_CONFIG.optional.key]: namesOf(AGENT_FORM_CONFIG.optional.fields),
  [AGENT_FORM_CONFIG.cost.key]: COST_FIELD_NAMES,
  [AGENT_FORM_CONFIG.litellm.key]: namesOf(AGENT_FORM_CONFIG.litellm.fields),
  [AUTH_HEADERS_PANEL_KEY]: ["static_headers", "extra_headers"],
};

export const unmountedA2AFieldNames = (mountedPanels: readonly string[]): readonly string[] =>
  Object.entries(A2A_PANEL_FIELD_NAMES)
    .filter(([panelKey]) => !mountedPanels.includes(panelKey))
    .flatMap(([, fieldNames]) => fieldNames);

interface AgentFormFieldsProps {
  panels: CollapsiblePanelsState;
  showAgentName?: boolean;
  visiblePanels?: string[];
}

const SkillsFieldArray = () => {
  const { t } = useTranslation();
  const skillFieldConfig = getSkillFieldConfig(t);
  const { control } = useFormContext<AgentFormValues>();
  const { fields, append, remove } = useFieldArray({ control, name: "skills" });

  return (
    <>
      {fields.map((item, index) => (
        <div key={item.id} className="rounded-md border border-border p-4">
          <FieldGroup>
            <AgentFormField
              name={`skills.${index}.id`}
              label={skillFieldConfig.id.label}
              rules={skillFieldConfig.id.required ? { required: t("ui.Required") } : undefined}
            >
              {({ value, onChange, ref, ...control }) => (
                <Input
                  {...control}
                  ref={ref}
                  placeholder={skillFieldConfig.id.placeholder}
                  value={typeof value === "string" ? value : ""}
                  onChange={onChange}
                />
              )}
            </AgentFormField>

            <AgentFormField
              name={`skills.${index}.name`}
              label={skillFieldConfig.name.label}
              rules={skillFieldConfig.name.required ? { required: t("ui.Required") } : undefined}
            >
              {({ value, onChange, ref, ...control }) => (
                <Input
                  {...control}
                  ref={ref}
                  placeholder={skillFieldConfig.name.placeholder}
                  value={typeof value === "string" ? value : ""}
                  onChange={onChange}
                />
              )}
            </AgentFormField>

            <AgentFormField
              name={`skills.${index}.description`}
              label={skillFieldConfig.description.label}
              rules={skillFieldConfig.description.required ? { required: t("ui.Required") } : undefined}
            >
              {({ value, onChange, ref, ...control }) => (
                <Textarea
                  {...control}
                  ref={ref}
                  rows={skillFieldConfig.description.rows}
                  placeholder={skillFieldConfig.description.placeholder}
                  value={typeof value === "string" ? value : ""}
                  onChange={onChange}
                />
              )}
            </AgentFormField>

            <AgentFormField
              name={`skills.${index}.tags`}
              label={skillFieldConfig.tags.label}
              rules={skillFieldConfig.tags.required ? { required: t("ui.Required") } : undefined}
            >
              {({ id, value, onChange }) => (
                <AgentTagsInput
                  id={id}
                  value={Array.isArray(value) ? (value as string[]) : []}
                  onValueChange={onChange}
                  placeholder={skillFieldConfig.tags.placeholder}
                />
              )}
            </AgentFormField>

            <AgentFormField name={`skills.${index}.examples`} label={skillFieldConfig.examples.label}>
              {({ id, value, onChange }) => (
                <AgentTagsInput
                  id={id}
                  value={Array.isArray(value) ? (value as string[]) : []}
                  onValueChange={onChange}
                  placeholder={skillFieldConfig.examples.placeholder}
                />
              )}
            </AgentFormField>
          </FieldGroup>

          <Button
            type="button"
            variant="ghost"
            className="mt-4 text-destructive hover:text-destructive/80"
            onClick={() => remove(index)}
          >
            <Trash2 />
            {t("ui.Remove Skill")}
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" className="w-full border-dashed" onClick={() => append({})}>
        <Plus />
        {t("ui.Add Skill")}
      </Button>
    </>
  );
};

const StaticHeadersFieldArray = () => {
  const { t } = useTranslation();
  const { control } = useFormContext<AgentFormValues>();
  const { fields, append, remove } = useFieldArray({ control, name: "static_headers" });

  return (
    <>
      {fields.map((item, index) => (
        <div key={item.id} className="flex items-start gap-2">
          <AgentFormField name={`static_headers.${index}.header`} rules={{ required: t("ui.Header name required") }}>
            {({ value, onChange, ref, ...control }) => (
              <Input
                {...control}
                ref={ref}
                className="w-55"
                placeholder={t("ui.Header name (e.g. Authorization)")}
                value={typeof value === "string" ? value : ""}
                onChange={onChange}
              />
            )}
          </AgentFormField>
          <AgentFormField name={`static_headers.${index}.value`} rules={{ required: t("ui.Value required") }}>
            {({ value, onChange, ref, ...control }) => (
              <Input
                {...control}
                ref={ref}
                className="w-65"
                placeholder={t("ui.Value (e.g. Bearer token123)")}
                value={typeof value === "string" ? value : ""}
                onChange={onChange}
              />
            )}
          </AgentFormField>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={t("ui.Remove static header")}
            className="text-destructive hover:text-destructive/80"
            onClick={() => remove(index)}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" className="w-full border-dashed" onClick={() => append({})}>
        <Plus />
        {t("ui.Add Static Header")}
      </Button>
    </>
  );
};

const AgentFormFields: React.FC<AgentFormFieldsProps> = ({ panels, showAgentName = true, visiblePanels }) => {
  const { t } = useTranslation();
  const agentFormConfig = getAgentFormConfig(t);
  const shouldShow = (key: string) => !visiblePanels || visiblePanels.includes(key);

  return (
    <>
      {showAgentName && (
        <FieldGroup className="mb-4">
          <AgentFormField
            name="agent_name"
            label={labelWithHint(t("ui.Agent Name"), t("ui.Unique identifier for the agent"))}
            rules={{ required: t("ui.Please enter a unique agent name") }}
          >
            {({ value, onChange, ref, ...control }) => (
              <Input
                {...control}
                ref={ref}
                placeholder={t("ui.e.g., customer-support-agent")}
                value={typeof value === "string" ? value : ""}
                onChange={onChange}
              />
            )}
          </AgentFormField>
        </FieldGroup>
      )}

      <div className="mb-4 rounded-md border border-border px-4">
        {shouldShow(agentFormConfig.basic.key) && (
          <AgentFormPanel
            panelKey={agentFormConfig.basic.key}
            title={`${agentFormConfig.basic.title} (Required)`}
            panels={panels}
          >
            {agentFormConfig.basic.fields.map((field) => (
              <AgentFormField
                key={field.name}
                name={field.name}
                label={field.tooltip ? labelWithHint(field.label, field.tooltip) : field.label}
                description={field.helpText}
                rules={field.required ? { required: `Please enter ${field.label.toLowerCase()}` } : undefined}
              >
                {({ value, onChange, ref, ...control }) => {
                  const text = typeof value === "string" ? value : "";
                  if (field.type === "textarea") {
                    return (
                      <Textarea
                        {...control}
                        ref={ref}
                        rows={field.rows}
                        placeholder={field.placeholder}
                        value={text}
                        onChange={onChange}
                      />
                    );
                  }
                  if (field.type === "select") {
                    return (
                      <Select value={text || null} onValueChange={onChange}>
                        <SelectTrigger {...control} className="w-full">
                          <SelectValue placeholder={field.placeholder} />
                        </SelectTrigger>
                        <SelectContent>
                          {(field.options ?? []).map((option) => (
                            <SelectItem key={option} value={option} title={option}>
                              {option}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    );
                  }
                  return (
                    <Input {...control} ref={ref} placeholder={field.placeholder} value={text} onChange={onChange} />
                  );
                }}
              </AgentFormField>
            ))}
          </AgentFormPanel>
        )}

        {shouldShow(agentFormConfig.skills.key) && (
          <AgentFormPanel panelKey={agentFormConfig.skills.key} title={agentFormConfig.skills.title} panels={panels}>
            <SkillsFieldArray />
          </AgentFormPanel>
        )}

        {shouldShow(agentFormConfig.capabilities.key) && (
          <AgentFormPanel
            panelKey={agentFormConfig.capabilities.key}
            title={agentFormConfig.capabilities.title}
            panels={panels}
          >
            {agentFormConfig.capabilities.fields.map((field) => (
              <AgentFormField key={field.name} name={field.name} label={field.label}>
                {({ value, onChange, ref, ...control }) => (
                  <Switch {...control} inputRef={ref} checked={value === true} onCheckedChange={onChange} />
                )}
              </AgentFormField>
            ))}
          </AgentFormPanel>
        )}

        {shouldShow(agentFormConfig.optional.key) && (
          <AgentFormPanel
            panelKey={agentFormConfig.optional.key}
            title={agentFormConfig.optional.title}
            panels={panels}
          >
            {agentFormConfig.optional.fields.map((field) => (
              <AgentFormField key={field.name} name={field.name} label={field.label}>
                {({ value, onChange, ref, ...control }) =>
                  field.type === "switch" ? (
                    <Switch {...control} inputRef={ref} checked={value === true} onCheckedChange={onChange} />
                  ) : (
                    <Input
                      {...control}
                      ref={ref}
                      placeholder={field.placeholder}
                      value={typeof value === "string" ? value : ""}
                      onChange={onChange}
                    />
                  )
                }
              </AgentFormField>
            ))}
          </AgentFormPanel>
        )}

        {shouldShow(agentFormConfig.cost.key) && (
          <AgentFormPanel panelKey={agentFormConfig.cost.key} title={agentFormConfig.cost.title} panels={panels}>
            <CostConfigFields />
          </AgentFormPanel>
        )}

        {shouldShow(agentFormConfig.litellm.key) && (
          <AgentFormPanel panelKey={agentFormConfig.litellm.key} title={agentFormConfig.litellm.title} panels={panels}>
            {agentFormConfig.litellm.fields.map((field) => (
              <AgentFormField key={field.name} name={field.name} label={field.label}>
                {({ value, onChange, ref, ...control }) =>
                  field.type === "switch" ? (
                    <Switch {...control} inputRef={ref} checked={value === true} onCheckedChange={onChange} />
                  ) : (
                    <Input
                      {...control}
                      ref={ref}
                      placeholder={field.placeholder}
                      value={typeof value === "string" ? value : ""}
                      onChange={onChange}
                    />
                  )
                }
              </AgentFormField>
            ))}
          </AgentFormPanel>
        )}

        {shouldShow(AUTH_HEADERS_PANEL_KEY) && (
          <AgentFormPanel panelKey={AUTH_HEADERS_PANEL_KEY} title={t("ui.Authentication Headers")} panels={panels}>
            <Field>
              <FieldTitle>
                {labelWithHint(
                  t("ui.Static Headers"),
                  t(
                    "ui.Headers always sent to the backend agent, regardless of the client request. Admin-configured, static wins on conflict.",
                  ),
                )}
              </FieldTitle>
              <div className="flex flex-col gap-2">
                <StaticHeadersFieldArray />
              </div>
            </Field>

            <AgentFormField
              name="extra_headers"
              label={labelWithHint(
                t("ui.Forward Client Headers"),
                t(
                  "ui.Header names to extract from the client's request and forward to the agent. Type a name and press Enter.",
                ),
              )}
            >
              {({ id, value, onChange }) => (
                <AgentTagsInput
                  id={id}
                  value={Array.isArray(value) ? (value as string[]) : []}
                  onValueChange={onChange}
                  placeholder={t("ui.e.g. x-api-key, Authorization")}
                />
              )}
            </AgentFormField>
          </AgentFormPanel>
        )}
      </div>
    </>
  );
};

export default AgentFormFields;
