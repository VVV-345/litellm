/**
 * Shared configuration for agent form fields
 * Used across create, view, and update operations
 */

type UiTranslate = (key: string) => string;

const identity: UiTranslate = (key) => key.replace(/^ui\./, "");

export interface FieldConfig {
  name: string;
  label: string;
  type: "text" | "textarea" | "url" | "switch" | "list" | "select";
  required?: boolean;
  tooltip?: string;
  placeholder?: string;
  defaultValue?: any;
  rows?: number;
  validation?: any[];
  options?: string[];
  helpText?: string;
}

export interface SectionConfig {
  key: string;
  title: string;
  fields: FieldConfig[];
  defaultExpanded?: boolean;
}

export const getAgentFormConfig = (t: UiTranslate = identity): {
  basic: SectionConfig;
  skills: SectionConfig;
  capabilities: SectionConfig;
  optional: SectionConfig;
  litellm: SectionConfig;
  cost: SectionConfig;
  tracing: SectionConfig;
} => ({
  basic: {
    key: "basic",
    title: t("ui.Basic Information"),
    defaultExpanded: true,
    fields: [
      {
        name: "name",
        label: t("ui.Display Name"),
        type: "text",
        required: true,
        placeholder: t("ui.e.g., Customer Support Agent"),
      },
      {
        name: "description",
        label: t("ui.Description"),
        type: "textarea",
        required: true,
        placeholder: t("ui.Describe what this agent does..."),
        rows: 3,
      },
      {
        name: "url",
        label: t("ui.URL"),
        type: "url",
        required: false,
        placeholder: "http://localhost:9999/",
        tooltip: t("ui.Base URL where the agent is hosted (optional)"),
      },
      {
        name: "version",
        label: t("ui.Version"),
        type: "text",
        placeholder: "1.0.0",
        defaultValue: "1.0.0",
      },
      {
        name: "protocolVersion",
        label: t("ui.Protocol Version"),
        type: "select",
        options: ["1.0", "0.3"],
        defaultValue: "1.0",
        tooltip: t(
          "ui.The A2A protocol version LiteLLM serves to clients for this agent. LiteLLM converts the upstream agent's responses to this version, so clients always see the version you pick here regardless of the original agent's version.",
        ),
        helpText: t(
          "ui.LiteLLM serves this version to clients and converts the upstream agent's responses to match it, regardless of the original agent's version.",
        ),
      },
    ],
  },
  skills: {
    key: "skills",
    title: t("ui.Skills"),
    fields: [
      {
        name: "skills",
        label: t("ui.Skills"),
        type: "list",
        defaultValue: [],
      },
    ],
  },
  capabilities: {
    key: "capabilities",
    title: t("ui.Capabilities"),
    fields: [
      {
        name: "streaming",
        label: t("ui.Streaming"),
        type: "switch",
        defaultValue: false,
      },
      {
        name: "pushNotifications",
        label: t("ui.Push Notifications"),
        type: "switch",
      },
      {
        name: "stateTransitionHistory",
        label: t("ui.State Transition History"),
        type: "switch",
      },
    ],
  },
  optional: {
    key: "optional",
    title: t("ui.Optional Settings"),
    fields: [
      {
        name: "iconUrl",
        label: t("ui.Icon URL"),
        type: "url",
        placeholder: "https://example.com/icon.png",
      },
      {
        name: "documentationUrl",
        label: t("ui.Documentation URL"),
        type: "url",
        placeholder: "https://docs.example.com",
      },
      {
        name: "supportsAuthenticatedExtendedCard",
        label: t("ui.Supports Authenticated Extended Card"),
        type: "switch",
      },
    ],
  },
  litellm: {
    key: "litellm",
    title: t("ui.LiteLLM Parameters"),
    fields: [
      {
        name: "model",
        label: t("ui.Model (Optional)"),
        type: "text",
      },
      {
        name: "make_public",
        label: t("ui.Make Public"),
        type: "switch",
      },
    ],
  },
  cost: {
    key: "cost",
    title: t("ui.Cost Configuration"),
    fields: [
      {
        name: "cost_per_query",
        label: t("ui.Cost Per Query ($)"),
        type: "text",
        placeholder: "0.0",
        tooltip: t("ui.Fixed cost per query"),
      },
      {
        name: "input_cost_per_token",
        label: t("ui.Input Cost Per Token ($)"),
        type: "text",
        placeholder: "0.000001",
        tooltip: t("ui.Cost per input token"),
      },
      {
        name: "output_cost_per_token",
        label: t("ui.Output Cost Per Token ($)"),
        type: "text",
        placeholder: "0.000002",
        tooltip: t("ui.Cost per output token"),
      },
    ],
  },
  tracing: {
    key: "tracing",
    title: t("ui.Tracing"),
    fields: [
      {
        name: "enable_tracing",
        label: t("ui.Enable Tracing"),
        type: "switch",
        defaultValue: false,
        tooltip: t("ui.Enable request tracing for this agent"),
      },
    ],
  },
});

export const getSkillFieldConfig = (t: UiTranslate = identity) => ({
  id: {
    name: "id",
    label: t("ui.Skill ID"),
    required: true,
    placeholder: t("ui.e.g., hello_world"),
  },
  name: {
    name: "name",
    label: t("ui.Skill Name"),
    required: true,
    placeholder: t("ui.e.g., Returns hello world"),
  },
  description: {
    name: "description",
    label: t("ui.Description"),
    required: true,
    placeholder: t("ui.What this skill does"),
    rows: 2,
  },
  tags: {
    name: "tags",
    label: t("ui.Tags"),
    required: true,
    placeholder: t("ui.Type a tag and press Enter"),
  },
  examples: {
    name: "examples",
    label: t("ui.Examples"),
    placeholder: t("ui.Type an example and press Enter"),
  },
});

export const AGENT_FORM_CONFIG = getAgentFormConfig();
export const SKILL_FIELD_CONFIG = getSkillFieldConfig();

/**
 * Get default form values from configuration
 */
export const getDefaultFormValues = () => {
  const defaults: any = {
    defaultInputModes: ["text"],
    defaultOutputModes: ["text"],
  };

  Object.values(getAgentFormConfig()).forEach((section) => {
    section.fields.forEach((field) => {
      if (field.defaultValue !== undefined) {
        defaults[field.name] = field.defaultValue;
      }
    });
  });

  return defaults;
};

/**
 * Build agent data from form values according to AgentConfig spec
 */
export const buildAgentDataFromForm = (values: any, existingAgent?: any) => {
  const agentData: any = {
    agent_name: values.agent_name,
    agent_card_params: {
      protocolVersion: values.protocolVersion || "1.0",
      name: values.name || values.agent_name,
      description: values.description || "",
      url: values.url || "",
      version: values.version || "1.0.0",
      defaultInputModes: existingAgent?.agent_card_params?.defaultInputModes || ["text"],
      defaultOutputModes: existingAgent?.agent_card_params?.defaultOutputModes || ["text"],
      capabilities: {
        streaming: values.streaming === true,
        ...(values.pushNotifications !== undefined && { pushNotifications: values.pushNotifications }),
        ...(values.stateTransitionHistory !== undefined && { stateTransitionHistory: values.stateTransitionHistory }),
      },
      skills: values.skills || [],
      ...(values.iconUrl && { iconUrl: values.iconUrl }),
      ...(values.documentationUrl && { documentationUrl: values.documentationUrl }),
      ...(values.supportsAuthenticatedExtendedCard !== undefined && {
        supportsAuthenticatedExtendedCard: values.supportsAuthenticatedExtendedCard,
      }),
    },
  };

  const params: Record<string, any> = {};

  if (values.model) params.model = values.model;
  if (values.make_public !== undefined) params.make_public = values.make_public;
  if (values.cost_per_query) params.cost_per_query = parseFloat(values.cost_per_query);
  if (values.input_cost_per_token) params.input_cost_per_token = parseFloat(values.input_cost_per_token);
  if (values.output_cost_per_token) params.output_cost_per_token = parseFloat(values.output_cost_per_token);

  if (Object.keys(params).length > 0) {
    agentData.litellm_params = params;
  }

  if (values.tpm_limit != null) agentData.tpm_limit = values.tpm_limit;
  if (values.rpm_limit != null) agentData.rpm_limit = values.rpm_limit;
  if (values.session_tpm_limit != null) agentData.session_tpm_limit = values.session_tpm_limit;
  if (values.session_rpm_limit != null) agentData.session_rpm_limit = values.session_rpm_limit;
  // static_headers: convert [{header, value}, ...] → {header: value, ...}
  if (Array.isArray(values.static_headers) && values.static_headers.length > 0) {
    const staticHeaders: Record<string, string> = {};
    values.static_headers.forEach((entry: { header?: string; value?: string }) => {
      const key = entry?.header?.trim();
      if (key) staticHeaders[key] = entry?.value ?? "";
    });
    if (Object.keys(staticHeaders).length > 0) {
      agentData.static_headers = staticHeaders;
    }
  }

  // extra_headers: already an array of strings from Select tags
  if (Array.isArray(values.extra_headers) && values.extra_headers.length > 0) {
    agentData.extra_headers = values.extra_headers;
  }

  return agentData;
};

/**
 * Parse agent data for form fields
 */
export const parseAgentForForm = (agent: any) => {
  const skills =
    agent.agent_card_params?.skills?.map((skill: any) => ({
      ...skill,
      tags: skill.tags,
      examples: skill.examples || [],
    })) || [];

  return {
    agent_name: agent.agent_name,
    name: agent.agent_card_params?.name,
    description: agent.agent_card_params?.description,
    url: agent.agent_card_params?.url,
    version: agent.agent_card_params?.version,
    protocolVersion: agent.agent_card_params?.protocolVersion,
    streaming: agent.agent_card_params?.capabilities?.streaming,
    pushNotifications: agent.agent_card_params?.capabilities?.pushNotifications,
    stateTransitionHistory: agent.agent_card_params?.capabilities?.stateTransitionHistory,
    skills: skills,
    iconUrl: agent.agent_card_params?.iconUrl,
    documentationUrl: agent.agent_card_params?.documentationUrl,
    supportsAuthenticatedExtendedCard: agent.agent_card_params?.supportsAuthenticatedExtendedCard,
    model: agent.litellm_params?.model,
    make_public: agent.litellm_params?.make_public,
    cost_per_query: agent.litellm_params?.cost_per_query,
    input_cost_per_token: agent.litellm_params?.input_cost_per_token,
    output_cost_per_token: agent.litellm_params?.output_cost_per_token,
    tpm_limit: agent.tpm_limit,
    rpm_limit: agent.rpm_limit,
    session_tpm_limit: agent.session_tpm_limit,
    session_rpm_limit: agent.session_rpm_limit,
    // static_headers: {key: value} → [{header, value}, ...]
    static_headers: agent.static_headers
      ? Object.entries(agent.static_headers as Record<string, string>).map(([header, value]) => ({
          header,
          value,
        }))
      : [],
    // extra_headers: already an array of strings
    extra_headers: agent.extra_headers ?? [],
  };
};
