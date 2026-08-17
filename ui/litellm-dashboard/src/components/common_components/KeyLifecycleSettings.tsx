import React, { useState } from "react";
import { Select, Tooltip, Divider, Switch, Checkbox, Form } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { TextInput } from "@tremor/react";
import { useTranslation } from "react-i18next";

const { Option } = Select;

interface KeyLifecycleSettingsProps {
  form: any; // Form instance from parent
  autoRotationEnabled: boolean;
  onAutoRotationChange: (enabled: boolean) => void;
  rotationInterval: string;
  onRotationIntervalChange: (interval: string) => void;
  isCreateMode?: boolean; // If true, shows "leave empty to never expire" instead of "-1 to never expire"
  neverExpire?: boolean;
  onNeverExpireChange?: (checked: boolean) => void;
}

const KeyLifecycleSettings: React.FC<KeyLifecycleSettingsProps> = ({
  form,
  autoRotationEnabled,
  onAutoRotationChange,
  rotationInterval,
  onRotationIntervalChange,
  isCreateMode = false,
  neverExpire = false,
  onNeverExpireChange,
}) => {
  const { t } = useTranslation();
  // Predefined intervals
  const predefinedIntervals = ["7d", "30d", "90d", "180d", "365d"];

  // Check if current interval is custom
  const isCustomInterval = rotationInterval && !predefinedIntervals.includes(rotationInterval);

  const [showCustomInput, setShowCustomInput] = useState(isCustomInterval);
  const [customInterval, setCustomInterval] = useState(isCustomInterval ? rotationInterval : "");

  const handleIntervalChange = (value: string) => {
    if (value === "custom") {
      setShowCustomInput(true);
      // Don't change the actual interval yet, wait for custom input
    } else {
      setShowCustomInput(false);
      setCustomInterval("");
      onRotationIntervalChange(value);
    }
  };

  const handleCustomIntervalChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setCustomInterval(value);
    onRotationIntervalChange(value);
  };

  return (
    <div className="space-y-6">
      {/* Key Expiry Section */}
      <div className="space-y-4">
        <span className="text-sm font-medium text-gray-700">{t("ui.Key Expiry Settings")}</span>

        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-700 flex items-center space-x-1">
            <span>{t("ui.Expire Key")}</span>
            <Tooltip title={t(
              "ui.Set when this key should expire. Format: 30s (seconds), 30m (minutes), 30h (hours), 30d (days). Leave empty to keep the current expiry unchanged.",
            )}>
              <InfoCircleOutlined className="text-gray-400 cursor-help text-xs" />
            </Tooltip>
            {!isCreateMode && onNeverExpireChange && (
              <Checkbox
                checked={neverExpire}
                onChange={(e) => {
                  const checked = e.target.checked;
                  onNeverExpireChange(checked);
                  if (checked) {
                    if (form && typeof form.setFieldValue === "function") {
                      form.setFieldValue("duration", "");
                    } else if (form && typeof form.setFieldsValue === "function") {
                      form.setFieldsValue({ duration: "" });
                    }
                  }
                }}
                className="ml-2 text-sm font-normal text-gray-600"
              >
                {t("ui.Never Expire")}
              </Checkbox>
            )}
          </label>
          <Form.Item name="duration" noStyle initialValue="">
            <TextInput
              placeholder={
                isCreateMode ? t("ui.e.g., 30d or leave empty to never expire") : t("ui.e.g., 30d")
              }
              className="w-full"
              disabled={!isCreateMode && neverExpire}
            />
          </Form.Item>
        </div>
      </div>

      <Divider />

      {/* Auto-Rotation Section */}
      <div className="space-y-4">
        <span className="text-sm font-medium text-gray-700">{t("ui.Auto-Rotation Settings")}</span>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700 flex items-center space-x-1">
              <span>{t("ui.Enable Auto-Rotation")}</span>
              <Tooltip title={t(
                "ui.Key will automatically regenerate at the specified interval for enhanced security.",
              )}>
                <InfoCircleOutlined className="text-gray-400 cursor-help text-xs" />
              </Tooltip>
            </label>
            <Switch
              checked={autoRotationEnabled}
              onChange={onAutoRotationChange}
              size="default"
              className={autoRotationEnabled ? "" : "bg-gray-400"}
            />
          </div>

          {autoRotationEnabled && (
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700 flex items-center space-x-1">
                <span>{t("ui.Rotation Interval")}</span>
                <Tooltip title={t(
                  "ui.How often the key should be automatically rotated. Choose the interval that best fits your security requirements.",
                )}>
                  <InfoCircleOutlined className="text-gray-400 cursor-help text-xs" />
                </Tooltip>
              </label>
              <div className="space-y-2">
                <Select
                  value={showCustomInput ? "custom" : rotationInterval}
                  onChange={handleIntervalChange}
                  className="w-full"
                  placeholder={t("ui.Select interval")}
                >
                  <Option value="7d">{t("ui.7 days")}</Option>
                  <Option value="30d">{t("ui.30 days")}</Option>
                  <Option value="90d">{t("ui.90 days")}</Option>
                  <Option value="180d">{t("ui.180 days")}</Option>
                  <Option value="365d">{t("ui.365 days")}</Option>
                  <Option value="custom">{t("ui.Custom interval")}</Option>
                </Select>

                {showCustomInput && (
                  <div className="space-y-1">
                    <TextInput
                      value={customInterval}
                      onChange={handleCustomIntervalChange}
                      placeholder={t("ui.e.g., 1s, 5m, 2h, 14d")}
                    />
                    <div className="text-xs text-gray-500">
                      {t("ui.Supported formats: seconds (s), minutes (m), hours (h), days (d)")}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {autoRotationEnabled && (
          <div className="bg-blue-50 p-3 rounded-md text-sm text-blue-700">
            {t(
              "ui.When rotation occurs, you'll receive a notification with the new key. The old key will be deactivated after a brief grace period.",
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default KeyLifecycleSettings;
