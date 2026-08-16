import { Form, Input, Select, Switch } from "antd";
import React from "react";
import { useTranslation } from "react-i18next";
import { CacheField } from "./cacheSettingsFields";

export interface EmbeddingModelOption {
  value: string;
  label: string;
}

export const SECRET_ALREADY_SET_PLACEHOLDER = "Already set. Enter a new value to replace it.";

interface CacheFormFieldProps {
  field: CacheField;
  embeddingModels: EmbeddingModelOption[];
  isSecretConfigured?: boolean;
}

const CacheFormField: React.FC<CacheFormFieldProps> = ({ field, embeddingModels, isSecretConfigured = false }) => {
  const { t } = useTranslation();
  const placeholder = isSecretConfigured ? t("ui.Already set. Enter a new value to replace it.") : t(field.helpText);

  const renderControl = (): React.ReactNode => {
    switch (field.type) {
      case "boolean":
        return <Switch />;
      case "password":
        return <Input.Password placeholder={placeholder} autoComplete="new-password" />;
      case "integer":
      case "float":
        return <Input inputMode="decimal" placeholder={placeholder} />;
      case "list":
        return <Input.TextArea rows={4} placeholder={placeholder} />;
      case "model-select":
        return (
          <Select
            showSearch
            allowClear
            placeholder={t("ui.Search and select a model...")}
            options={embeddingModels}
            optionFilterProp="label"
            style={{ width: "100%" }}
          />
        );
      default:
        return <Input placeholder={placeholder} />;
    }
  };

  return (
    <Form.Item
      name={field.name}
      label={t(field.label)}
      extra={t(field.helpText)}
      rules={field.rules}
      valuePropName={field.type === "boolean" ? "checked" : "value"}
    >
      {renderControl()}
    </Form.Item>
  );
};

export default CacheFormField;
