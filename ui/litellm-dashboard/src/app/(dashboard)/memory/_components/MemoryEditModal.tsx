"use client";

import React, { useEffect, useState } from "react";
import { Form, Input, Modal, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { MemoryRow } from "@/components/networking";

const { Text } = Typography;

interface MemoryEditModalProps {
  open: boolean;
  mode: "create" | "edit";
  initialRow?: MemoryRow;
  onClose: () => void;
  onSave: (key: string, value: string, metadataText: string, isCreate: boolean) => Promise<boolean>;
}

export const MemoryEditModal: React.FC<MemoryEditModalProps> = ({ open, mode, initialRow, onClose, onSave }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (mode === "edit" && initialRow) {
      form.setFieldsValue({
        key: initialRow.key,
        value: initialRow.value,
        metadata: initialRow.metadata != null ? JSON.stringify(initialRow.metadata, null, 2) : "",
      });
    } else {
      form.resetFields();
    }
  }, [open, mode, initialRow, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    const ok = await onSave(values.key.trim(), values.value ?? "", values.metadata ?? "", mode === "create");
    setSubmitting(false);
    if (ok) {
      form.resetFields();
      onClose();
    }
  };

  return (
    <Modal
      open={open}
      title={mode === "create" ? t("ui.Create memory") : `Edit ${initialRow?.key ?? ""}`}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      onOk={handleOk}
      okText={mode === "create" ? t("ui.Create") : t("ui.Save")}
      confirmLoading={submitting}
      width={640}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          label={t("ui.Key")}
          name="key"
          rules={[{ required: true, message: t("ui.Key is required") }]}
          tooltip={t("ui.Globally unique — two memories cannot share a key. Namespace your own keys if you need per-user isolation (e.g. user:123:notes).")}
        >
          <Input placeholder={t("ui.e.g. user_role")} disabled={mode === "edit"} />
        </Form.Item>
        <Form.Item
          label={t("ui.Value")}
          name="value"
          rules={[{ required: true, message: t("ui.Value is required") }]}
          tooltip={t("ui.Markdown/text injected into LLM context. Plain strings are fine.")}
        >
          <Input.TextArea rows={8} placeholder={t("ui.What the agent should remember…")} />
        </Form.Item>
        <Form.Item
          label={
            <span>
              {t("ui.Metadata")} <Text type="secondary">{t("ui.(optional JSON)")}</Text>
            </span>
          }
          name="metadata"
          tooltip={t("ui.Optional structured metadata — must be valid JSON if provided.")}
        >
          <Input.TextArea
            rows={4}
            placeholder='{"tags": ["example"]}'
            style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default MemoryEditModal;
