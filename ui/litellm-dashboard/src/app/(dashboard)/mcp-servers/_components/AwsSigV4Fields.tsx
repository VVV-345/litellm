import React from "react";
import { Form, Input, Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";

const AwsSigV4Fields: React.FC = () => {
  const { t } = useTranslation();
  return (
    <>
      <p className="text-sm text-gray-500 mb-2">
        {t("ui.For MCP servers hosted on AWS Bedrock AgentCore.")}{" "}
        <a
          href="https://docs.litellm.ai/docs/mcp_aws_sigv4"
          target="_blank"
          rel="noopener noreferrer"
          className="text-blue-500 hover:text-blue-700"
        >
          {t("ui.View docs &rarr;")}
        </a>
      </p>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Region")}
            <Tooltip title={t("ui.AWS region for SigV4 signing (e.g., us-east-1)")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_region_name"]}
        rules={[{ required: true, message: t("ui.AWS region is required for SigV4 auth") }]}
      >
        <Input placeholder="us-east-1" className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500" />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Service Name")}
            <Tooltip title={t("ui.AWS service name for SigV4 signing. Defaults to 'bedrock-agentcore'.")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_service_name"]}
      >
        <Input
          placeholder="bedrock-agentcore"
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Access Key ID")}
            <Tooltip title={t("ui.Optional. If not provided, falls back to the boto3 credential chain (IAM role, env vars, etc.).")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_access_key_id"]}
        dependencies={[["credentials", "aws_secret_access_key"]]}
        rules={[
          ({ getFieldValue }) => ({
            validator(_, value) {
              const secretKey = getFieldValue(["credentials", "aws_secret_access_key"]);
              if (secretKey && !value) {
                return Promise.reject(new Error(t("ui.Access Key ID is required when Secret Access Key is provided")));
              }
              return Promise.resolve();
            },
          }),
        ]}
      >
        <Input.Password
          placeholder={t("ui.AKIA... (optional — uses IAM role if blank)")}
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Secret Access Key")}
            <Tooltip title={t("ui.Optional. Required if AWS Access Key ID is provided.")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_secret_access_key"]}
        dependencies={[["credentials", "aws_access_key_id"]]}
        rules={[
          ({ getFieldValue }) => ({
            validator(_, value) {
              const accessKeyId = getFieldValue(["credentials", "aws_access_key_id"]);
              if (accessKeyId && !value) {
                return Promise.reject(new Error(t("ui.Secret Access Key is required when Access Key ID is provided")));
              }
              return Promise.resolve();
            },
          }),
        ]}
      >
        <Input.Password
          placeholder={t("ui.Enter secret key (optional — uses IAM role if blank)")}
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Session Token")}
            <Tooltip title={t("ui.Optional. Only needed for temporary STS credentials.")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_session_token"]}
      >
        <Input.Password
          placeholder={t("ui.Enter session token (optional)")}
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Role ARN")}
            <Tooltip title={t("ui.Optional. IAM role ARN to assume via STS before signing. If set, LiteLLM calls sts:AssumeRole to get temporary credentials. Uses ambient credentials (IAM role, env vars) as the source identity unless explicit keys are also provided.")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_role_name"]}
      >
        <Input
          placeholder={t("ui.arn:aws:iam::123456789012:role/MyRole (optional)")}
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
      <Form.Item
        label={
          <span className="text-sm font-medium text-gray-700 flex items-center">
            {t("ui.AWS Session Name")}
            <Tooltip title={t("ui.Optional. Session name for the AssumeRole call — appears in CloudTrail logs. Auto-generated if omitted.")}>
              <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
            </Tooltip>
          </span>
        }
        name={["credentials", "aws_session_name"]}
      >
        <Input
          placeholder={t("ui.litellm-prod (optional, auto-generated if blank)")}
          className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
        />
      </Form.Item>
    </>
  );
};

export default AwsSigV4Fields;
