import { InfoCircleOutlined } from "@ant-design/icons";
import { Select as AntdSelect, Tooltip, Typography } from "antd";
import React from "react";
import { useTranslation } from "react-i18next";

const { Text } = Typography;

export const DEFAULT_ESCALATION_KEYWORDS = ["LITELLM ESCALATE"];

interface EscalationKeywordsProps {
  keywords: string[];
  onChange: (keywords: string[]) => void;
}

const EscalationKeywords: React.FC<EscalationKeywordsProps> = ({ keywords, onChange }) => {
  const { t } = useTranslation();
  return (
    <div className="w-full max-w-none">
      <div className="flex items-center gap-2 mb-1">
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("ui.Escalation Keywords")}
        </Typography.Title>
        <Tooltip title={t("ui.Case-sensitive phrases a user can include in their message to force a bump to the next-higher complexity tier when they aren't happy with results. They can force a stronger model, but not choose which one.")}>
          <InfoCircleOutlined className="text-gray-400" />
        </Tooltip>
      </div>
      <Text type="secondary" style={{ display: "block", marginBottom: 8, fontSize: 12 }}>
        {t("ui.Optional: when a user message contains one of these phrases, the request is bumped one tier higher than it would otherwise route to. Matching is case-sensitive, so \"LITELLM ESCALATE\" only fires on the exact, shouted form. Leave empty to disable.")}
      </Text>
      <AntdSelect
        mode="tags"
        value={keywords}
        onChange={onChange}
        placeholder={t("ui.e.g., LITELLM ESCALATE")}
        tokenSeparators={[","]}
        open={false}
        suffixIcon={null}
        style={{ width: "100%" }}
        allowClear
      />
    </div>
  );
};

export default EscalationKeywords;
