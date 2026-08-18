// 本文件提供添加模型流程共用的模型选择器，支持已知模型、多选和直接输入自定义模型。
import React from "react";
import { Form } from "antd";
import { useTranslation } from "react-i18next";
import { TextInput, Text } from "@tremor/react";
import { Row, Col } from "antd";
import { Providers } from "../provider_info_helpers";
import CreatableModelSelect from "./CreatableModelSelect";

interface LiteLLMModelNameFieldProps {
  selectedProvider: Providers;
  providerModels: string[];
  getPlaceholder: (provider: Providers) => string;
}

const LiteLLMModelNameField: React.FC<LiteLLMModelNameFieldProps> = ({
  selectedProvider,
  providerModels,
  getPlaceholder,
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const handleModelChange = (value: string | string[]) => {
    const values = Array.isArray(value) ? value : [value];

    if (values.includes("all-wildcard")) {
      form.setFieldsValue({ model_name: undefined, model_mappings: [] });
    } else {
      // Form.Item 会先写 model，再调用这里；映射必须无条件同步，否则新输入模型无法提交。
      const mappings = values.map((model) => {
        if (selectedProvider === Providers.Azure) {
          return {
            public_name: model,
            litellm_model: `azure/${model}`,
          };
        }
        return {
          public_name: model,
          litellm_model: model,
        };
      });

      form.setFieldsValue({
        model: values,
        model_mappings: mappings,
      });
    }
  };

  const handleAzureDeploymentNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const deploymentName = e.target.value;

    const mappings = deploymentName
      ? [
          {
            public_name: deploymentName,
            litellm_model: `azure/${deploymentName}`,
          },
        ]
      : [];

    form.setFieldsValue({
      model: deploymentName,
      model_mappings: mappings,
    });
  };

  return (
    <>
      <Form.Item
        label={t("ui.LiteLLM Model Name(s)")}
        tooltip={t("ui.The model name LiteLLM will send to the LLM API")}
        className="mb-0"
      >
        <Form.Item
          name="model"
          rules={[
            {
              required: true,
              message:
                selectedProvider === Providers.Azure
                  ? t("ui.Please enter a deployment name.")
                  : t("ui.Please enter at least one model."),
            },
          ]}
          noStyle
        >
          {selectedProvider === Providers.Azure ||
          selectedProvider === Providers.OpenAI_Compatible ||
          selectedProvider === Providers.Ollama ? (
            <>
              <TextInput
                placeholder={getPlaceholder(selectedProvider)}
                onChange={selectedProvider === Providers.Azure ? handleAzureDeploymentNameChange : undefined}
              />
            </>
          ) : providerModels.length > 0 ? (
            <CreatableModelSelect
              testId="model-name-select"
              models={providerModels}
              placeholder={t("ui.Select models or enter a custom model name")}
              onChange={handleModelChange}
              extraOptions={[
                {
                  label: t("ui.All {{provider}} Models (Wildcard)", { provider: selectedProvider }),
                  value: "all-wildcard",
                },
              ]}
            />
          ) : (
            <TextInput placeholder={getPlaceholder(selectedProvider)} />
          )}
        </Form.Item>
      </Form.Item>
      <Row>
        <Col span={10}></Col>
        <Col span={14}>
          <Text className="mb-3 mt-1">
            {selectedProvider === Providers.Azure
              ? t(
                  "ui.Your deployment name will be saved as the public model name, and LiteLLM will use 'azure/deployment-name' internally",
                )
              : t("ui.The model name LiteLLM will send to the LLM API")}
          </Text>
        </Col>
      </Row>
    </>
  );
};

export default LiteLLMModelNameField;
