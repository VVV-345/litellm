// 代理管理接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";

export interface AgentCredentialFieldMetadata {
  key: string;
  label: string;
  placeholder?: string | null;
  tooltip?: string | null;
  required?: boolean;
  field_type?: "text" | "password" | "select" | "upload" | "textarea";
  options?: string[] | null;
  default_value?: string | null;
  include_in_litellm_params?: boolean;
}

export interface AgentCreateInfo {
  agent_type: string;
  agent_type_display_name: string;
  description?: string | null;
  logo_url?: string | null;
  credential_fields: AgentCredentialFieldMetadata[];
  litellm_params_template?: Record<string, string> | null;
  model_template?: string | null;
  use_a2a_form_fields?: boolean;
}

export const getAgentCreateMetadata = async (): Promise<AgentCreateInfo[]> => {
  /**
   * Fetch agent type metadata from the proxy's public endpoint.
   * This is used by the UI to dynamically render agent-specific credential fields.
   */
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/agents/fields` : `/public/agents/fields`;
  const response = await fetch(url, {
    method: "GET",
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error("Failed to fetch agent create metadata:", response.status, errorText);
    throw new Error("Failed to load agent configuration");
  }

  const jsonData: AgentCreateInfo[] = await response.json();
  return jsonData;
};

export const agentHubPublicModelsCall = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/agent_hub` : `/public/agent_hub`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    console.error(`agentHubPublicModelsCall failed with status ${response.status}`);
    return [];
  }
  return response.json();
};

export const createAgentCall = async (accessToken: string, agentData: any) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents` : `/v1/agents`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...agentData,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to create agent:", error);
    throw error;
  }
};

export interface DiscoveredAgentCard {
  protocolVersion?: string;
  name?: string;
  description?: string;
  version?: string;
  url?: string;
  iconUrl?: string;
  documentationUrl?: string;
  defaultInputModes?: string[];
  defaultOutputModes?: string[];
  capabilities?: Record<string, any>;
  skills?: Array<{
    id?: string;
    name?: string;
    description?: string;
    tags?: string[];
    examples?: string[];
    [key: string]: any;
  }>;
  provider?: { organization?: string; url?: string };
  [key: string]: any;
}

export interface DiscoverAgentCardResponse {
  url: string;
  agent_card: DiscoveredAgentCard;
}

/**
 * How the backend should locate the upstream agent card.
 *
 * - ``well_known_fallback`` (default): pure A2A — try the three standard
 *   well-known paths under the base URL.
 * - ``langgraph_platform``: LangGraph Platform — hits the canonical
 *   well-known path with an ``assistant_id`` query parameter, because
 *   LangGraph mounts one shared card endpoint per deployment.
 */
export type DiscoveryMode = "well_known_fallback" | "langgraph_platform";

export interface DiscoverAgentCardOptions {
  discovery_mode?: DiscoveryMode;
  /** Mode-specific params. ``langgraph_platform`` requires ``assistant_id``. */
  params?: Record<string, any>;
}

export const discoverAgentCardCall = async (
  accessToken: string,
  url: string,
  options?: DiscoverAgentCardOptions,
): Promise<DiscoverAgentCardResponse> => {
  const endpoint = proxyBaseUrl ? `${proxyBaseUrl}/v1/a2a/discover` : `/v1/a2a/discover`;
  const body: Record<string, any> = { url };
  if (options?.discovery_mode) body.discovery_mode = options.discovery_mode;
  if (options?.params) body.params = options.params;

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errorData = await response.text();
    handleError(errorData);
    throw new Error(errorData);
  }

  return (await response.json()) as DiscoverAgentCardResponse;
};

// Re-export Team

export const deleteAgentCall = async (accessToken: string, agentId: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents/${agentId}` : `/v1/agents/${agentId}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to delete agent:", error);
    throw error;
  }
};

export const makeAgentsPublicCall = async (accessToken: string, agentIds: string[]) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents/make_public` : `/v1/agents/make_public`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        agent_ids: agentIds,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to make agents public:", error);
    throw error;
  }
};

export const getAgentsList = async (accessToken: string, healthCheck: boolean = false) => {
  try {
    const params = healthCheck ? "?health_check=true" : "";
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents${params}` : `/v1/agents${params}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to get agents list");
    }

    const data = await response.json();
    return { agents: data };
  } catch (error) {
    console.error("Failed to get agents list:", error);
    throw error;
  }
};

export const getAgentInfo = async (accessToken: string, agentId: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents/${agentId}` : `/v1/agents/${agentId}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to get agent info");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get agent info:", error);
    throw error;
  }
};

export const patchAgentCall = async (
  accessToken: string,
  agentId: string,
  updateData: {
    agent_name?: string;
    litellm_params?: Record<string, any>;
    agent_card_params?: Record<string, any>;
    tpm_limit?: number | null;
    rpm_limit?: number | null;
    session_tpm_limit?: number | null;
    session_rpm_limit?: number | null;
  },
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/agents/${agentId}` : `/v1/agents/${agentId}`;

    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(updateData),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to patch agent");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update guardrail:", error);
    throw error;
  }
};
