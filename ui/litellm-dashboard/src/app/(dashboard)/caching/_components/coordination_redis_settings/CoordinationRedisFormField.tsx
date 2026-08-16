import { Form, Input, Switch } from "antd";
import React from "react";
import { useTranslation } from "react-i18next";
import { CoordinationField } from "./coordinationRedisFields";

export const SECRET_ALREADY_SET_PLACEHOLDER = "Already set. Enter a new value to replace it.";

interface CoordinationRedisFormFieldProps {
  field: CoordinationField;
  isSecretConfigured: boolean;
}

const renderControl = (field: CoordinationField, placeholder: string): React.ReactNode => {
  switch (field.type) {
    case "boolean":
      return <Switch />;
    case "password":
      return <Input.Password placeholder={placeholder} autoComplete="new-password" />;
    case "integer":
      return <Input inputMode="numeric" placeholder={placeholder} />;
    case "list":
      return <Input.TextArea rows={4} placeholder={placeholder} />;
    default:
      return <Input placeholder={placeholder} />;
  }
};

const CoordinationRedisFormField: React.FC<CoordinationRedisFormFieldProps> = ({ field, isSecretConfigured }) => {
  const { t } = useTranslation();
  return (
    <Form.Item
      name={field.name}
      label={t(field.label)}
      extra={t(field.helpText)}
      rules={field.rules}
      valuePropName={field.type === "boolean" ? "checked" : "value"}
    >
      {renderControl(field, isSecretConfigured ? t("ui.Already set. Enter a new value to replace it.") : t(field.helpText))}
    </Form.Item>
  );
};

export default CoordinationRedisFormField;
