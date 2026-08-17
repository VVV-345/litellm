import { Alert, Button, Form, Input, Modal, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { modelPatchUpdateCall } from "./networking";
import NotificationsManager from "./molecules/notifications_manager";

const { Text } = Typography;

interface UpdateModelCredentialsModalProps {
  open: boolean;
  onCancel: () => void;
  accessToken: string;
  modelId: string;
  onUpdated: () => void;
}

export default function UpdateModelCredentialsModal({
  open,
  onCancel,
  accessToken,
  modelId,
  onUpdated,
}: UpdateModelCredentialsModalProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [isSaving, setIsSaving] = useState(false);

  const close = () => {
    form.resetFields();
    onCancel();
  };

  const handleSubmit = async (values: { api_key?: string }) => {
    const apiKey = values.api_key?.trim();
    if (!apiKey) {
      NotificationsManager.fromBackend(t("ui.Enter a new API key"));
      return;
    }
    setIsSaving(true);
    try {
      await modelPatchUpdateCall(
        accessToken,
        { litellm_params: { api_key: apiKey }, model_info: { id: modelId } },
        modelId,
      );
      NotificationsManager.success(t("ui.API key updated"));
      form.resetFields();
      onUpdated();
      onCancel();
    } catch (error) {
      console.error("Error updating API key:", error);
      NotificationsManager.fromBackend(t("ui.Failed to update API key"));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Modal title={t("ui.Update API Key")} open={open} onCancel={close} footer={null} width={520} destroyOnHidden={true}>
      <Text className="block mb-4 text-gray-500">
        {t("ui.Update this model's API key. Only the new key is sent; the rest of the deployment configuration is left untouched.")}
      </Text>
      <Alert
        type="warning"
        showIcon
        className="mb-4"
        message={t("ui.Only the API key is rotated here. Models that authenticate with an Azure AD token, AWS credentials, or a Vertex service-account JSON aren't supported yet; update those from the model's LiteLLM Params for now.")}
      />
      <Form form={form} onFinish={handleSubmit} layout="vertical">
        <Form.Item
          label={t("ui.New API Key")}
          name="api_key"
          rules={[{ required: true, message: t("ui.Enter a new API key") }]}
        >
          <Input.Password placeholder={t("ui.Enter the new API key")} autoComplete="new-password" />
        </Form.Item>
        <div className="flex justify-end items-center mt-4">
          <Button onClick={close} style={{ marginRight: 10 }}>
            {t("ui.Cancel")}
          </Button>
          <Button type="primary" htmlType="submit" loading={isSaving}>
            {t("ui.Update API Key")}
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
