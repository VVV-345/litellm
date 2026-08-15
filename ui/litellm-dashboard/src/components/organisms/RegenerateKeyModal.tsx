import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { CheckOutlined, CopyOutlined, SyncOutlined } from "@ant-design/icons";
import { Alert, Button, Col, Flex, Form, Input, InputNumber, Modal, Row, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { KeyResponse } from "../key_team_helpers/key_list";
import NotificationManager from "../molecules/notifications_manager";
import { regenerateKeyCall } from "../networking";
import { calculateExpiryPreviewFromDuration, formatExpiresUtc, isKeyExpired } from "@/utils/keyExpiryUtils";

const { Text } = Typography;

const DURATION_RULE = {
  pattern: /^(\d+(s|m|h|d|w|mo))?$/,
  message: "Must be a duration like 30s, 30m, 24h, 2d, 1w, or 1mo",
};

interface RegenerateKeyModalProps {
  selectedToken: KeyResponse | null;
  visible: boolean;
  onClose: () => void;
  onKeyUpdate?: (updatedKeyData: Partial<KeyResponse>) => void;
}

export function RegenerateKeyModal({ selectedToken, visible, onClose, onKeyUpdate }: RegenerateKeyModalProps) {
  const { t } = useTranslation();
  const { accessToken } = useAuthorized();
  const [form] = Form.useForm();
  const [regeneratedKey, setRegeneratedKey] = useState<string | null>(null);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);

  const keyIsExpired = isKeyExpired(selectedToken?.expires);
  const durationValue = Form.useWatch("duration", form);

  // Expired keys must get a new duration, otherwise regeneration produces a key
  // that inherits the old (past) expiry and is immediately unusable.
  const durationRules = keyIsExpired
    ? [{ required: true, message: t("ui.Expiration is required for expired keys") }, DURATION_RULE]
    : [DURATION_RULE];

  useEffect(() => {
    if (visible && selectedToken && accessToken) {
      form.setFieldsValue({
        key_alias: selectedToken.key_alias,
        max_budget: selectedToken.max_budget,
        tpm_limit: selectedToken.tpm_limit,
        rpm_limit: selectedToken.rpm_limit,
        duration: selectedToken.duration || "",
        grace_period: "",
      });
    }
  }, [visible, selectedToken, form, accessToken]);

  const newExpiryTime = durationValue ? calculateExpiryPreviewFromDuration(durationValue) : null;

  const handleRegenerateKey = async () => {
    if (!selectedToken || !accessToken) return;

    setIsRegenerating(true);
    try {
      const formValues = await form.validateFields();

      const response = await regenerateKeyCall(accessToken, selectedToken.token || selectedToken.token_id, formValues);
      setRegeneratedKey(response.key);
      NotificationManager.success("Virtual Key regenerated successfully");

      // Build the update payload. Spread the API response first so any new
      // fields it returns (new token, timestamps, etc.) are captured, then
      // override with the explicit form values — the user's just-submitted
      // edits must win over whatever the API echoes back.
      // expires must come from the API (an ISO string), never the locale-
      // formatted preview, otherwise downstream expiry parsing breaks.
      const updatedKeyData: Partial<KeyResponse> = {
        ...response,
        token: response.token || response.key_id || selectedToken.token,
        key_name: response.key,
        max_budget: formValues.max_budget,
        tpm_limit: formValues.tpm_limit,
        rpm_limit: formValues.rpm_limit,
        expires: response.expires ?? selectedToken.expires,
      };

      // Update the parent component with new key data
      if (onKeyUpdate) {
        onKeyUpdate(updatedKeyData);
      }

      setIsRegenerating(false);
    } catch (error) {
      setIsRegenerating(false); // Reset regenerating state on error
      // Ant Design form validation rejections surface inline under the field;
      // don't also raise a backend-style toast for them.
      if (error && typeof error === "object" && "errorFields" in error) {
        return;
      }
      console.error("Error regenerating key:", error);
      NotificationManager.fromBackend(error);
    }
  };

  const handleClose = () => {
    setRegeneratedKey(null);
    setIsRegenerating(false);
    setCopied(false);
    form.resetFields();
    onClose();
  };

  const handleCopyKey = () => {
    setCopied(true);
  };

  return (
    <Modal
      title={t("ui.Regenerate Virtual Key")}
      open={visible}
      onCancel={handleClose}
      width={520}
      maskClosable={false}
      footer={
        regeneratedKey
          ? [
              <Space key="footer-actions">
                <Button onClick={handleClose}>{t("ui.Close")}</Button>
                <CopyToClipboard text={regeneratedKey} onCopy={handleCopyKey}>
                  <Button type="primary" icon={copied ? <CheckOutlined /> : <CopyOutlined />}>
                    {copied ? t("ui.Copied") : t("ui.Copy Key")}
                  </Button>
                </CopyToClipboard>
              </Space>,
            ]
          : [
              <Space key="footer-actions">
                <Button onClick={handleClose}>{t("ui.Cancel")}</Button>
                <Button type="primary" icon={<SyncOutlined />} onClick={handleRegenerateKey} loading={isRegenerating}>
                  {t("ui.Regenerate")}
                </Button>
              </Space>,
            ]
      }
    >
      {regeneratedKey ? (
        <Flex vertical gap="middle">
          <Alert type="warning" showIcon message={t("ui.Save it now, you will not see it again")} />

          <Flex vertical gap={2}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.Key Alias")}
            </Text>
            <Text>{selectedToken?.key_alias || t("ui.No alias set")}</Text>
          </Flex>

          <Flex vertical gap={6}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.Virtual Key")}
            </Text>
            <div
              style={{
                background: "#f5f5f5",
                border: "1px solid #e8e8e8",
                borderRadius: 6,
                padding: "14px 16px",
                fontFamily: "SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace",
                fontSize: 16,
                wordBreak: "break-all",
                color: "#262626",
              }}
            >
              {regeneratedKey}
            </div>
          </Flex>
        </Flex>
      ) : (
        <Form form={form} layout="vertical" style={{ marginTop: 4 }}>
          <Form.Item name="key_alias" label={t("ui.Key Alias")}>
            <Input disabled />
          </Form.Item>

          <Row gutter={12}>
            <Col span={8}>
              <Form.Item name="max_budget" label={t("ui.Max Budget (USD)")}>
                <InputNumber step={0.01} precision={2} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="tpm_limit" label={t("ui.TPM Limit")}>
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="rpm_limit" label={t("ui.RPM Limit")}>
                <InputNumber style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={12}>
            <Col span={12}>
              <Form.Item
                name="duration"
                label={t("ui.Expire Key")}
                rules={durationRules}
                extra={
                  <Flex vertical gap={2}>
                    <Text type={keyIsExpired ? "danger" : "secondary"} style={{ fontSize: 12 }}>
                      Current expiry: {selectedToken?.expires ? formatExpiresUtc(selectedToken.expires) : t("ui.Never")}
                      {keyIsExpired && " (expired)"}
                    </Text>
                    {newExpiryTime && (
                      <Text type="success" style={{ fontSize: 12 }}>
                        New expiry: {newExpiryTime}
                      </Text>
                    )}
                  </Flex>
                }
              >
                <Input placeholder="e.g. 30s, 30h, 30d" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="grace_period"
                label={t("ui.Grace Period")}
                tooltip="Keep the old key valid for this duration after rotation. Both keys work during this period for seamless cutover. Empty = immediate revoke."
                extra={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Recommended: 24h to 72h for production keys
                  </Text>
                }
                rules={[DURATION_RULE]}
              >
                <Input placeholder="e.g. 24h, 2d" />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      )}
    </Modal>
  );
}
