// MCP 服务管理接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, HTTP_REQUEST, globalLitellmHeaderName, apiClient, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export const mcpHubPublicServersCall = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/mcp_hub` : `/public/mcp_hub`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    console.error(`mcpHubPublicServersCall failed with status ${response.status}`);
    return [];
  }
  return response.json();
};

export const fetchOpenAPIRegistry = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/mcp/openapi-registry` : `/v1/mcp/openapi-registry`;

    const response = await fetch(url, {
      method: HTTP_REQUEST.GET,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(deriveErrorMessage(errorData));
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to fetch OpenAPI registry:", error);
    throw error;
  }
};

export const fetchDiscoverableMCPServers = async (accessToken: string) => {
  try {
    return await apiClient.get(`/v1/mcp/discover`, { accessToken });
  } catch (error) {
    console.error("Failed to fetch discoverable MCP servers:", error);
    throw error;
  }
};

export const fetchMCPServers = async (accessToken: string, teamId?: string | null, connectedAppView?: boolean) => {
  try {
    return await apiClient.get(`/v1/mcp/server`, {
      accessToken,
      query: { team_id: teamId || undefined, connected_app_view: connectedAppView || undefined },
    });
  } catch (error) {
    console.error("Failed to fetch MCP servers:", error);
    throw error;
  }
};

export const fetchMCPServerHealth = async (accessToken: string, serverIds?: string[]) => {
  try {
    return await apiClient.get(`/v1/mcp/server/health`, {
      accessToken,
      query: {
        server_ids: serverIds && serverIds.length > 0 ? serverIds : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch MCP server health:", error);
    throw error;
  }
};

export const fetchMCPAccessGroups = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/v1/mcp/access_groups`, { accessToken });
    return data.access_groups || [];
  } catch (error) {
    console.error("Failed to fetch MCP access groups:", error);
    throw error;
  }
};

export const fetchMCPClientIp = async (accessToken: string): Promise<string | null> => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/mcp/network/client-ip` : `/v1/mcp/network/client-ip`;

    const response = await fetch(url, {
      method: HTTP_REQUEST.GET,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      return null;
    }

    const data = await response.json();
    return data.ip || null;
  } catch {
    return null;
  }
};

export const createMCPServer = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.post(`/v1/mcp/server`, {
      accessToken,
      body: {
        ...formValues, // Include formValues in the request body
      },
    });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const importMCPServers = async (accessToken: string, payload: Record<string, unknown>) => {
  try {
    return await apiClient.post(`/v1/mcp/server/import`, { accessToken, body: payload });
  } catch (error) {
    console.error("Failed to import MCP servers:", error);
    throw error;
  }
};

export const updateMCPServer = async (accessToken: string, formValues: Record<string, any>) => {
  try {
    return await apiClient.put(`/v1/mcp/server`, { accessToken, body: formValues });
  } catch (error) {
    console.error("Failed to update MCP server:", error);
    throw error;
  }
};

export const deleteMCPServer = async (accessToken: string, serverId: string) => {
  try {
    await apiClient.delete(`/v1/mcp/server/${serverId}`, { accessToken });
  } catch (error) {
    console.error("Failed to delete key:", error);
    throw error;
  }
};

export const fetchMCPToolsets = async (accessToken: string): Promise<any[]> => {
  try {
    return await apiClient.get(`/v1/mcp/toolset`, { accessToken });
  } catch (error) {
    console.error("Failed to fetch MCP toolsets:", error);
    throw error;
  }
};

export const createMCPToolset = async (accessToken: string, formValues: Record<string, any>) => {
  try {
    return await apiClient.post(`/v1/mcp/toolset`, { accessToken, body: formValues });
  } catch (error) {
    console.error("Failed to create MCP toolset:", error);
    throw error;
  }
};

export const updateMCPToolset = async (accessToken: string, formValues: Record<string, any>) => {
  try {
    return await apiClient.put(`/v1/mcp/toolset`, { accessToken, body: formValues });
  } catch (error) {
    console.error("Failed to update MCP toolset:", error);
    throw error;
  }
};

export const deleteMCPToolset = async (accessToken: string, toolsetId: string) => {
  try {
    await apiClient.delete(`/v1/mcp/toolset/${toolsetId}`, { accessToken });
  } catch (error) {
    console.error("Failed to delete MCP toolset:", error);
    throw error;
  }
};

export const registerMCPServer = async (accessToken: string, formValues: Record<string, any>) => {
  try {
    return await apiClient.post(`/v1/mcp/server/register`, { accessToken, body: formValues });
  } catch (error) {
    console.error("Failed to register MCP server:", error);
    throw error;
  }
};

export const fetchMCPSubmissions = async (accessToken: string) => {
  try {
    const url = (proxyBaseUrl ? `${proxyBaseUrl}` : "") + `/v1/mcp/server/submissions`;
    const response = await fetch(url, {
      method: HTTP_REQUEST.GET,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }
    return response.json();
  } catch (error) {
    console.error("Failed to fetch MCP submissions:", error);
    throw error;
  }
};

export const approveMCPServer = async (accessToken: string, serverId: string) => {
  try {
    const url = (proxyBaseUrl ? `${proxyBaseUrl}` : "") + `/v1/mcp/server/${encodeURIComponent(serverId)}/approve`;
    const response = await fetch(url, {
      method: HTTP_REQUEST.PUT,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }
    return response.json();
  } catch (error) {
    console.error("Failed to approve MCP server:", error);
    throw error;
  }
};

export const rejectMCPServer = async (accessToken: string, serverId: string, reviewNotes?: string) => {
  try {
    const url = (proxyBaseUrl ? `${proxyBaseUrl}` : "") + `/v1/mcp/server/${encodeURIComponent(serverId)}/reject`;
    const response = await fetch(url, {
      method: HTTP_REQUEST.PUT,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ review_notes: reviewNotes ?? null }),
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }
    return response.json();
  } catch (error) {
    console.error("Failed to reject MCP server:", error);
    throw error;
  }
};

export const makeMCPPublicCall = async (accessToken: string, mcpServerIds: string[]) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/mcp/make_public` : `/v1/mcp/make_public`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        mcp_server_ids: mcpServerIds,
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
