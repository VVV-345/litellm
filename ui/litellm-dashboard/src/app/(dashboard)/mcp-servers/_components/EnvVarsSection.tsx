import React from "react";
import { useTranslation } from "react-i18next";
import { translateUiText } from "@/utils/i18nText";
import { Form, Input, Select, Button, Tooltip, Typography } from "antd";
import { InfoCircleOutlined, MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";

const { Text } = Typography;

const SCOPE_OPTIONS = [
  { value: "global", label: "Instance" },
  { value: "user", label: "Per-user" },
];

/**
 * Form section for admin-configured MCP environment variables.
 *
 * Each row has: name | value | scope. Variables can be interpolated into
 * Static Headers via ${NAME}. ``scope=global`` (shown as "Instance") values
 * are used as-is. ``scope=user`` (shown as "Per-user") values are filled in
 * by each user via the MCP Gateway dashboard.
 *
 * The parent form reads the ``env_vars`` field from the form values.
 */
const EnvVarsSection: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="flex items-center gap-2 mb-1">
        <Text strong className="text-sm">
          {t("ui.Variables")}
        </Text>
        <Tooltip
          title={
            <>
              {t("ui.Define variables you can interpolate in Static Headers or Authentication using")}{" "}
              <code>{"${VAR_NAME}"}</code>. <br />
              <b>{t("ui.Instance")}</b>: {t("ui.admin-defined value used for every user.")}
              <br />
              <b>{t("ui.Per-user")}</b>:{" "}
              {t("ui.each user supplies their own value (e.g. personal credentials) via the MCP Gateway dashboard.")}
            </>
          }
        >
          <InfoCircleOutlined className="text-blue-400 hover:text-blue-600 cursor-help" />
        </Tooltip>
      </div>
      <Text className="text-xs text-gray-600 block mb-3">
        {t("ui.Reference these in Static Headers or Authentication as")} <code>{"${VAR_NAME}"}</code>. {t("ui.For example:")}{" "}
        <code className="bg-white px-1 rounded-sm border border-gray-200">
          {"${DB_PROTOCOL}://${CORP_USERNAME}:${CORP_PASSWORD}@${DB_HOSTNAME}"}
        </code>
      </Text>

      <Form.List name="env_vars">
        {(fields, { add, remove }) => (
          <div className="space-y-2">
            {fields.length > 0 && (
              <div className="flex gap-3 px-1 text-xs font-medium text-gray-500 uppercase tracking-wide">
                <div style={{ flex: 1 }}>{t("ui.Variable Name")}</div>
                <div style={{ flex: 1 }}>{t("ui.Value / Description")}</div>
                <div style={{ width: 160 }}>{t("ui.Scope")}</div>
                <div style={{ width: 24 }} />
              </div>
            )}
            {fields.map(({ key, name, ...restField }) => (
              <div key={key} className="flex gap-3 items-start">
                <Form.Item
                  {...restField}
                  name={[name, "name"]}
                  className="mb-0"
                  style={{ flex: 1 }}
                  rules={[
                    { required: true, message: t("ui.Variable name is required") },
                    {
                      pattern: /^[A-Za-z_][A-Za-z0-9_]*$/,
                      message: t("ui.Use letters, digits, underscores; cannot start with a digit."),
                    },
                  ]}
                >
                  <Input placeholder={t("ui.e.g. DB_PROTOCOL")} className="rounded-md font-mono" />
                </Form.Item>
                <div style={{ flex: 1 }}>
                  <ScopedValueOrDescription name={name} restField={restField} />
                </div>
                <Form.Item
                  {...restField}
                  name={[name, "scope"]}
                  className="mb-0"
                  initialValue="global"
                  style={{ width: 160 }}
                >
                  <Select
                    options={SCOPE_OPTIONS.map((o) => ({ ...o, label: t(`ui.${o.label}`) }))}
                  />
                </Form.Item>
                <div style={{ width: 24, height: 32 }} className="flex items-center justify-center">
                  <MinusCircleOutlined
                    onClick={() => remove(name)}
                    className="text-gray-500 hover:text-red-500 cursor-pointer"
                  />
                </div>
              </div>
            ))}
            <Button type="dashed" onClick={() => add({ scope: "global" })} icon={<PlusOutlined />} block>
              {translateUiText(t, "Add Variable")}
            </Button>
          </div>
        )}
      </Form.List>
    </div>
  );
};

// For instance-scoped vars this column holds the admin value. For per-user
// vars the value comes from each user later, so the column instead captures an
// optional description that the per-user fill-in modal shows as a hint.
const ScopedValueOrDescription: React.FC<{
  name: number;
  restField: object;
}> = ({ name, restField }) => {
  const { t } = useTranslation();
  const isPerUser = Form.useWatch(["env_vars", name, "scope"]) === "user";
  if (isPerUser) {
    return (
      <Form.Item {...restField} name={[name, "description"]} className="mb-0">
        <Input
          addonBefore={
            <Tooltip title={t("ui.Per-user variables have no shared value. This text is only a hint shown to each user when they fill in their own value.")}>
              <span className="text-xs text-gray-500 cursor-help whitespace-nowrap">
                <InfoCircleOutlined className="mr-1" />
                {t("ui.Hint")}
              </span>
            </Tooltip>
          }
          placeholder={t("ui.e.g. Your DB username")}
          styles={{ input: { color: "#9ca3af" } }}
        />
      </Form.Item>
    );
  }
  return (
    <Form.Item {...restField} name={[name, "value"]} className="mb-0">
      <Input placeholder={t("ui.e.g. postgresql")} className="rounded-md font-mono" />
    </Form.Item>
  );
};

export default EnvVarsSection;
