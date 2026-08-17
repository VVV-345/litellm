import React from "react";
import { useTranslation } from "react-i18next";
import { Form, Input, Switch, Collapse, Select, Space, Tooltip } from "antd";
import { Button as AntButton } from "antd";
import { PlusOutlined, MinusCircleOutlined, InfoCircleOutlined } from "@ant-design/icons";
import { getAgentFormConfig, getSkillFieldConfig } from "./agent_config";

import CostConfigFields from "./cost_config_fields";

const { Panel } = Collapse;

interface AgentFormFieldsProps {
  showAgentName?: boolean;
  visiblePanels?: string[];
}

/**
 * Reusable form fields component for agent forms
 * Uses shared configuration from agent_config.ts
 */
const AgentFormFields: React.FC<AgentFormFieldsProps> = ({ showAgentName = true, visiblePanels }) => {
  const { t } = useTranslation();
  const agentFormConfig = getAgentFormConfig(t);
  const skillFieldConfig = getSkillFieldConfig(t);
  const shouldShow = (key: string) => !visiblePanels || visiblePanels.includes(key);
  return (
    <>
      {showAgentName && (
        <Form.Item
          label={t("ui.Agent Name")}
          name="agent_name"
          rules={[{ required: true, message: t("ui.Please enter a unique agent name") }]}
          tooltip={t("ui.Unique identifier for the agent")}
        >
          <Input placeholder={t("ui.e.g., customer-support-agent")} />
        </Form.Item>
      )}

      <Collapse defaultActiveKey={["basic"]} style={{ marginBottom: 16 }}>
        {/* Basic Information */}
        {shouldShow(agentFormConfig.basic.key) && (
          <Panel
            header={`${agentFormConfig.basic.title} (${t("ui.Required")})`}
            key={agentFormConfig.basic.key}
          >
            {agentFormConfig.basic.fields.map((field) => (
              <Form.Item
                key={field.name}
                label={field.label}
                name={field.name}
                rules={
                  field.required
                    ? [
                        {
                          required: true,
                          message: t(`ui.Please enter ${field.label.toLowerCase()}`, {
                            defaultValue: `Please enter ${field.label.toLowerCase()}`,
                          }),
                        },
                      ]
                    : undefined
                }
                tooltip={field.tooltip}
                extra={field.helpText}
              >
                {field.type === "textarea" ? (
                  <Input.TextArea rows={field.rows} placeholder={field.placeholder} />
                ) : field.type === "select" ? (
                  <Select placeholder={field.placeholder}>
                    {(field.options ?? []).map((opt) => (
                      <Select.Option key={opt} value={opt}>
                        {opt}
                      </Select.Option>
                    ))}
                  </Select>
                ) : (
                  <Input placeholder={field.placeholder} />
                )}
              </Form.Item>
            ))}
          </Panel>
        )}

        {/* Skills */}
        {shouldShow(agentFormConfig.skills.key) && (
          <Panel header={agentFormConfig.skills.title} key={agentFormConfig.skills.key}>
            <Form.List name="skills">
              {(fields, { add, remove }) => (
                <>
                  {fields.map((field) => (
                    <div
                      key={field.key}
                      style={{ marginBottom: 16, padding: 16, border: "1px solid #d9d9d9", borderRadius: 4 }}
                    >
                      <Form.Item
                        {...field}
                        label={skillFieldConfig.id.label}
                        name={[field.name, "id"]}
                        rules={[{ required: skillFieldConfig.id.required, message: t("ui.Required") }]}
                      >
                        <Input placeholder={skillFieldConfig.id.placeholder} />
                      </Form.Item>

                      <Form.Item
                        {...field}
                        label={skillFieldConfig.name.label}
                        name={[field.name, "name"]}
                        rules={[{ required: skillFieldConfig.name.required, message: t("ui.Required") }]}
                      >
                        <Input placeholder={skillFieldConfig.name.placeholder} />
                      </Form.Item>

                      <Form.Item
                        {...field}
                        label={skillFieldConfig.description.label}
                        name={[field.name, "description"]}
                        rules={[{ required: skillFieldConfig.description.required, message: t("ui.Required") }]}
                      >
                        <Input.TextArea
                          rows={skillFieldConfig.description.rows}
                          placeholder={skillFieldConfig.description.placeholder}
                        />
                      </Form.Item>

                      <Form.Item
                        {...field}
                        label={skillFieldConfig.tags.label}
                        name={[field.name, "tags"]}
                        rules={[{ required: skillFieldConfig.tags.required, message: t("ui.Required") }]}
                      >
                        <Select
                          mode="tags"
                          style={{ width: "100%" }}
                          tokenSeparators={[","]}
                          placeholder={skillFieldConfig.tags.placeholder}
                        />
                      </Form.Item>

                      <Form.Item {...field} label={skillFieldConfig.examples.label} name={[field.name, "examples"]}>
                        <Select
                          mode="tags"
                          style={{ width: "100%" }}
                          tokenSeparators={[","]}
                          placeholder={skillFieldConfig.examples.placeholder}
                        />
                      </Form.Item>

                      <AntButton type="link" danger onClick={() => remove(field.name)} icon={<MinusCircleOutlined />}>
                        {t("ui.Remove Skill")}
                      </AntButton>
                    </div>
                  ))}
                  <AntButton type="dashed" onClick={() => add()} icon={<PlusOutlined />} style={{ width: "100%" }}>
                    {t("ui.Add Skill")}
                  </AntButton>
                </>
              )}
            </Form.List>
          </Panel>
        )}

        {/* Capabilities */}
        {shouldShow(agentFormConfig.capabilities.key) && (
          <Panel header={agentFormConfig.capabilities.title} key={agentFormConfig.capabilities.key}>
            {agentFormConfig.capabilities.fields.map((field) => (
              <Form.Item key={field.name} label={field.label} name={field.name} valuePropName="checked">
                <Switch />
              </Form.Item>
            ))}
          </Panel>
        )}

        {/* Optional Settings */}
        {shouldShow(agentFormConfig.optional.key) && (
          <Panel header={agentFormConfig.optional.title} key={agentFormConfig.optional.key}>
            {agentFormConfig.optional.fields.map((field) => (
              <Form.Item
                key={field.name}
                label={field.label}
                name={field.name}
                valuePropName={field.type === "switch" ? "checked" : undefined}
              >
                {field.type === "switch" ? <Switch /> : <Input placeholder={field.placeholder} />}
              </Form.Item>
            ))}
          </Panel>
        )}

        {/* Cost Configuration */}
        {shouldShow(agentFormConfig.cost.key) && (
          <Panel header={agentFormConfig.cost.title} key={agentFormConfig.cost.key}>
            <CostConfigFields />
          </Panel>
        )}

        {/* LiteLLM Parameters */}
        {shouldShow(agentFormConfig.litellm.key) && (
          <Panel header={agentFormConfig.litellm.title} key={agentFormConfig.litellm.key}>
            {agentFormConfig.litellm.fields.map((field) => (
              <Form.Item
                key={field.name}
                label={field.label}
                name={field.name}
                valuePropName={field.type === "switch" ? "checked" : undefined}
              >
                {field.type === "switch" ? <Switch /> : <Input placeholder={field.placeholder} />}
              </Form.Item>
            ))}
          </Panel>
        )}

        {/* Authentication Headers */}
        {shouldShow("auth_headers") && (
          <Panel header={t("ui.Authentication Headers")} key="auth_headers">
            {/* Static Headers */}
            <Form.Item
              label={
                <span>
                  {t("ui.Static Headers")}{" "}
                  <Tooltip title={t("ui.Headers always sent to the backend agent, regardless of the client request. Admin-configured, static wins on conflict.")}>
                    <InfoCircleOutlined style={{ color: "#8c8c8c" }} />
                  </Tooltip>
                </span>
              }
            >
              <Form.List name="static_headers">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name, ...restField }) => (
                      <Space key={key} style={{ display: "flex", marginBottom: 8 }} align="baseline">
                        <Form.Item
                          {...restField}
                          name={[name, "header"]}
                          rules={[{ required: true, message: t("ui.Header name required") }]}
                        >
                          <Input placeholder={t("ui.Header name (e.g. Authorization)")} style={{ width: 220 }} />
                        </Form.Item>
                        <Form.Item
                          {...restField}
                          name={[name, "value"]}
                          rules={[{ required: true, message: t("ui.Value required") }]}
                        >
                          <Input placeholder={t("ui.Value (e.g. Bearer token123)")} style={{ width: 260 }} />
                        </Form.Item>
                        <MinusCircleOutlined onClick={() => remove(name)} style={{ color: "#ff4d4f" }} />
                      </Space>
                    ))}
                    <AntButton type="dashed" onClick={() => add()} icon={<PlusOutlined />} style={{ width: "100%" }}>
                      {t("ui.Add Static Header")}
                    </AntButton>
                  </>
                )}
              </Form.List>
            </Form.Item>

            {/* Extra Headers (dynamic forwarding) */}
            <Form.Item
              label={
                <span>
                  {t("ui.Forward Client Headers")}{" "}
                  <Tooltip title={t("ui.Header names to extract from the client's request and forward to the agent. Type a name and press Enter.")}>
                    <InfoCircleOutlined style={{ color: "#8c8c8c" }} />
                  </Tooltip>
                </span>
              }
              name="extra_headers"
            >
              <Select
                mode="tags"
                style={{ width: "100%" }}
                placeholder={t("ui.e.g. x-api-key, Authorization")}
                tokenSeparators={[","]}
              />
            </Form.Item>
          </Panel>
        )}
      </Collapse>
    </>
  );
};

export default AgentFormFields;
