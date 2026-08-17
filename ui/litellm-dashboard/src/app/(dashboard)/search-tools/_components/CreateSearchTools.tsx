import { isAdminRole } from "@/utils/roles";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Button, TextInput } from "@tremor/react";
import { Form, Input, Modal, Select, Tooltip, Typography } from "antd";
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Logo } from "@/components/molecules/logo/Logo";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { createSearchTool, fetchAvailableSearchProviders } from "@/components/networking";
import SearchConnectionTest from "./SearchConnectionTest";
import { AvailableSearchProvider, SearchTool } from "./types";
import dataforseoLogo from "../../../../../public/assets/logos/dataforseo.png";
import exaAiLogo from "../../../../../public/assets/logos/exa_ai.png";
import googlePseLogo from "../../../../../public/assets/logos/google_pse.png";
import parallelAiLogo from "../../../../../public/assets/logos/parallel_ai.png";
import perplexityLogo from "../../../../../public/assets/logos/perplexity.png";
import tavilyLogo from "../../../../../public/assets/logos/tavily.png";

const { TextArea } = Input;

const searchProviderLogoMap: Record<string, string> = {
  perplexity: perplexityLogo.src,
  tavily: tavilyLogo.src,
  parallel_ai: parallelAiLogo.src,
  exa_ai: exaAiLogo.src,
  google_pse: googlePseLogo.src,
  dataforseo: dataforseoLogo.src,
};

interface SearchProviderLabelProps {
  providerName: string;
  displayName: string;
}

export const SearchProviderLabel: React.FC<SearchProviderLabelProps> = ({ providerName, displayName }) => (
  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
    <Logo src={searchProviderLogoMap[providerName]} label={displayName} className="w-5 h-5 object-contain" />
    <span>{displayName}</span>
  </div>
);

interface CreateSearchToolProps {
  userRole: string;
  accessToken: string | null;
  onCreateSuccess: (newSearchTool: SearchTool) => void;
  isModalVisible: boolean;
  setModalVisible: (visible: boolean) => void;
}

const CreateSearchTool: React.FC<CreateSearchToolProps> = ({
  userRole,
  accessToken,
  onCreateSuccess,
  isModalVisible,
  setModalVisible,
}) => {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [isLoading, setIsLoading] = useState(false);
  const [formValues, setFormValues] = useState<Record<string, any>>({});
  const [isTestModalVisible, setIsTestModalVisible] = useState(false);
  const [isTestingConnection, setIsTestingConnection] = useState(false);
  const [connectionTestId, setConnectionTestId] = useState<string>("");

  // Fetch available search providers
  const { data: providersResponse, isLoading: isLoadingProviders } = useQuery({
    queryKey: ["searchProviders"],
    queryFn: () => {
      if (!accessToken) throw new Error("Access Token required");
      return fetchAvailableSearchProviders(accessToken);
    },
    enabled: !!accessToken && isModalVisible,
  }) as { data: { providers: AvailableSearchProvider[] }; isLoading: boolean };

  const availableProviders = providersResponse?.providers || [];

  const handleCreate = async (formValues: Record<string, any>) => {
    setIsLoading(true);
    try {
      // Prepare the payload
      const payload = {
        search_tool_name: formValues.search_tool_name,
        litellm_params: {
          search_provider: formValues.search_provider,
          api_key: formValues.api_key,
          api_base: formValues.api_base,
          timeout: formValues.timeout ? parseFloat(formValues.timeout) : undefined,
          max_retries: formValues.max_retries ? parseInt(formValues.max_retries) : undefined,
        },
        search_tool_info: formValues.description
          ? {
              description: formValues.description,
            }
          : undefined,
      };

      if (accessToken != null) {
        const response = await createSearchTool(accessToken, payload);

        NotificationsManager.success(t("ui.Search tool created successfully"));
        form.resetFields();
        setFormValues({});
        setModalVisible(false);
        onCreateSuccess(response);
      }
    } catch (error) {
      NotificationsManager.error(t("ui.Error creating search tool") + ": " + error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setFormValues({});
    setModalVisible(false);
  };

  const handleTestConnection = async () => {
    try {
      // Validate required fields for testing
      await form.validateFields(["search_provider", "api_key"]);

      setIsTestingConnection(true);
      // Generate a new test ID (using timestamp for uniqueness)
      setConnectionTestId(`test-${Date.now()}`);
      // Show the modal with the fresh test
      setIsTestModalVisible(true);
    } catch (error) {
      NotificationsManager.error(t("ui.Please fill in Search Provider and API Key before testing"));
    }
  };

  // Clear formValues when modal closes to reset
  React.useEffect(() => {
    if (!isModalVisible) {
      setFormValues({});
    }
  }, [isModalVisible]);

  if (!isAdminRole(userRole)) {
    return null;
  }

  return (
    <Modal
      title={
        <div className="flex items-center space-x-3 pb-4 border-b border-gray-100">
          <span className="text-2xl">🔍</span>
          <h2 className="text-xl font-semibold text-gray-900">{t("ui.Add New Search Tool")}</h2>
        </div>
      }
      open={isModalVisible}
      width={800}
      onCancel={handleCancel}
      footer={null}
      className="top-8"
      styles={{
        body: { padding: "24px" },
        header: { padding: "24px 24px 0 24px", border: "none" },
      }}
    >
      <div className="mt-6">
        <Form
          form={form}
          onFinish={handleCreate}
          onValuesChange={(_, allValues) => setFormValues(allValues)}
          layout="vertical"
          className="space-y-6"
        >
          <div className="grid grid-cols-1 gap-6">
            <Form.Item
              label={
                <span className="text-sm font-medium text-gray-700 flex items-center">
                  {t("ui.Search Tool Name")}
                  <Tooltip title={t("ui.A unique name to identify this search tool configuration (e.g., 'perplexity-search', 'tavily-news-search').")}>
                    <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
                  </Tooltip>
                </span>
              }
              name="search_tool_name"
              rules={[
                { required: true, message: t("ui.Please enter a search tool name") },
                {
                  pattern: /^[a-zA-Z0-9_-]+$/,
                  message: t("ui.Name can only contain letters, numbers, hyphens, and underscores"),
                },
              ]}
            >
              <TextInput
                placeholder={t("ui.e.g., perplexity-search, my-tavily-tool")}
                className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
              />
            </Form.Item>

            <Form.Item
              label={
                <span className="text-sm font-medium text-gray-700 flex items-center">
                  {t("ui.Search Provider")}
                  <Tooltip title={t("ui.Select the search provider you want to use. Each provider has different capabilities and pricing.")}>
                    <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
                  </Tooltip>
                </span>
              }
              name="search_provider"
              rules={[{ required: true, message: t("ui.Please select a search provider") }]}
            >
              <Select
                placeholder={t("ui.Select a search provider")}
                className="rounded-lg"
                size="large"
                loading={isLoadingProviders}
                showSearch
                optionFilterProp="children"
                optionLabelProp="label"
              >
                {availableProviders.map((provider) => (
                  <Select.Option
                    key={provider.provider_name}
                    value={provider.provider_name}
                    label={
                      <SearchProviderLabel
                        providerName={provider.provider_name}
                        displayName={provider.ui_friendly_name}
                      />
                    }
                  >
                    <SearchProviderLabel
                      providerName={provider.provider_name}
                      displayName={provider.ui_friendly_name}
                    />
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>

            <Form.Item
              label={
                <span className="text-sm font-medium text-gray-700 flex items-center">
                  {t("ui.API Key")}
                  <Tooltip title={t("ui.The API key for authenticating with the search provider. This will be securely stored.")}>
                    <InfoCircleOutlined className="ml-2 text-blue-400 hover:text-blue-600 cursor-help" />
                  </Tooltip>
                </span>
              }
              name="api_key"
              rules={[{ required: false, message: t("ui.Please enter an API key") }]}
            >
              <TextInput
                type="password"
                placeholder={t("ui.Enter your API key")}
                className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
              />
            </Form.Item>

            <Form.Item
              label={
                <span className="text-sm font-medium text-gray-700">
                  {t("ui.Description")} ({t("ui.Optional")})
                </span>
              }
              name="description"
            >
              <TextArea
                rows={3}
                placeholder={t("ui.Brief description of this search tool's purpose")}
                className="rounded-lg border-gray-300 focus:border-blue-500 focus:ring-blue-500"
              />
            </Form.Item>
          </div>

          <div className="flex justify-between items-center pt-6 border-t border-gray-100">
            <Tooltip title={t("ui.Get help on our github")}>
              <Typography.Link href="https://github.com/BerriAI/litellm/issues" target="_blank">
                {t("ui.Need Help?")}
              </Typography.Link>
            </Tooltip>
            <div className="space-x-2">
              <Button onClick={handleTestConnection} loading={isTestingConnection}>
                {t("ui.Test Connection")}
              </Button>
              <Button loading={isLoading} type="submit">
                {t("ui.Add Search Tool")}
              </Button>
            </div>
          </div>
        </Form>
      </div>

      {/* Test Connection Results Modal */}
      <Modal
        title={t("ui.Connection Test Results")}
        open={isTestModalVisible}
        onCancel={() => {
          setIsTestModalVisible(false);
          setIsTestingConnection(false);
        }}
        footer={[
          <Button
            key="close"
            onClick={() => {
              setIsTestModalVisible(false);
              setIsTestingConnection(false);
            }}
          >
            {t("ui.Close")}
          </Button>,
        ]}
        width={700}
      >
        {/* Only render the SearchConnectionTest when modal is visible and we have a test ID */}
        {isTestModalVisible && accessToken && (
          <SearchConnectionTest
            key={connectionTestId}
            litellmParams={{
              search_provider: formValues.search_provider,
              api_key: formValues.api_key,
              api_base: formValues.api_base,
            }}
            accessToken={accessToken}
            onTestComplete={() => setIsTestingConnection(false)}
          />
        )}
      </Modal>
    </Modal>
  );
};

export default CreateSearchTool;
