import { useCloudZeroUpdateSettings } from "@/app/(dashboard)/hooks/cloudzero/useCloudZeroSettings";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Form, Input, Modal } from "antd";
import MessageManager from "@/components/molecules/message_manager";
import { useEffect } from "react";
import { CloudZeroSettings } from "./types";
import { useTranslation } from "react-i18next";

interface CloudZeroUpdateModalProps {
  open: boolean;
  onOk: () => void;
  onCancel: () => void;
  settings: CloudZeroSettings;
}

export default function CloudZeroUpdateModal({ open, onOk, onCancel, settings }: CloudZeroUpdateModalProps) {
  const { t } = useTranslation();
  const { accessToken } = useAuthorized();
  const [form] = Form.useForm();
  const updateMutation = useCloudZeroUpdateSettings(accessToken || "");

  useEffect(() => {
    if (open && settings) {
      form.setFieldsValue({
        connection_id: settings.connection_id,
        timezone: settings.timezone || "UTC",
        api_key: "",
      });
    } else if (open) {
      form.resetFields();
    }
  }, [open, settings, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      updateMutation.mutate(
        {
          connection_id: values.connection_id,
          timezone: values.timezone || "UTC",
          ...(values.api_key && { api_key: values.api_key }),
        },
        {
          onSuccess: () => {
            MessageManager.success(t("ui.CloudZero integration updated successfully"));
            form.resetFields();
            onOk();
          },
          onError: (error: any) => {
            if (error?.errorFields) {
              return;
            }
            MessageManager.error(error?.message || t("ui.Failed to update CloudZero integration"));
          },
        },
      );
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      MessageManager.error(error?.message || t("ui.Failed to update CloudZero integration"));
    }
  };

  const handleCancel = () => {
    form.resetFields();
    onCancel();
  };

  return (
    <Modal
      title={t("ui.Edit CloudZero Integration")}
      open={open}
      onOk={handleSubmit}
      onCancel={handleCancel}
      confirmLoading={updateMutation.isPending}
      okText={updateMutation.isPending ? t("ui.Updating...") : t("ui.Update")}
      cancelText={t("ui.Cancel")}
      okButtonProps={{
        disabled: updateMutation.isPending,
      }}
      cancelButtonProps={{
        disabled: updateMutation.isPending,
      }}
    >
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item
          label={t("ui.CloudZero API Key")}
          name="api_key"
          rules={[{ required: false, message: t("ui.Please enter your CloudZero API key") }]}
          tooltip={t("ui.Leave empty to keep the existing API key")}
        >
          <Input.Password placeholder={t("ui.Leave empty to keep existing")} />
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
