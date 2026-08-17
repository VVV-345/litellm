import { Form, Modal, Input } from "antd";
import MessageManager from "@/components/molecules/message_manager";
import { useEffect } from "react";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useCloudZeroCreate } from "@/app/(dashboard)/hooks/cloudzero/useCloudZeroCreate";
import { useTranslation } from "react-i18next";

interface CloudZeroCreationModalProps {
  open: boolean;
  onOk: () => void;
  onCancel: () => void;
}

export default function CloudZeroCreationModal({ open, onOk, onCancel }: CloudZeroCreationModalProps) {
  const { t } = useTranslation();
  const { accessToken } = useAuthorized();
  const [form] = Form.useForm();
  const createMutation = useCloudZeroCreate(accessToken || "");

  useEffect(() => {
    if (open) {
      form.resetFields();
    }
  }, [open, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      createMutation.mutate(
        {
          connection_id: values.connection_id,
          timezone: values.timezone || "UTC",
          ...(values.api_key && { api_key: values.api_key }),
        },
        {
          onSuccess: () => {
            MessageManager.success(t("ui.CloudZero integration created successfully"));
            form.resetFields();
            onOk();
          },
          onError: (error: any) => {
            if (error?.errorFields) {
              return;
            }
            MessageManager.error(error?.message || t("ui.Failed to create CloudZero integration"));
          },
        },
      );
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      MessageManager.error(error?.message || t("ui.Failed to create CloudZero integration"));
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onCancel();
  };

  return (
    <Modal
      title={t("ui.Create CloudZero Integration")}
      open={open}
      onOk={handleSubmit}
      onCancel={handleCancel}
      confirmLoading={createMutation.isPending}
      okText={createMutation.isPending ? t("ui.Creating...") : t("ui.Create")}
      cancelText={t("ui.Cancel")}
      okButtonProps={{
        disabled: createMutation.isPending,
      }}
      cancelButtonProps={{
        disabled: createMutation.isPending,
      }}
    >
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          label={t("ui.CloudZero API Key")}
          name="api_key"
          rules={[{ required: true, message: t("ui.Please enter your CloudZero API key") }]}
        >
          <Input.Password placeholder={t("ui.Enter your CloudZero API key")} />
        </Form.Item>
        <Form.Item
          label={t("ui.Connection ID")}
          name="connection_id"
          rules={[{ required: true, message: t("ui.Please enter your CloudZero connection ID") }]}
        >
          <Input placeholder={t("ui.Enter your CloudZero connection ID")} />
        </Form.Item>
        <Form.Item
          label={t("ui.Timezone")}
          name="timezone"
          tooltip={t("ui.Timezone for date handling (defaults to UTC if not provided)")}
        >
          <Input placeholder="UTC" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
