// MCP 工具调用接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import { MCP_TOOLS_PREVIEW_FORBIDDEN_MESSAGE } from "@/components/mcp_tools/constants";

export const getMCPSemanticFilterSettings = async (accessToken: string) => {
  /**
   * Get MCP semantic filter configuration
   */
  try {
    const data = await apiClient.get(`/get/mcp_semantic_filter_settings`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get MCP semantic filter settings:", error);
    throw error;
  }
};

export const updateMCPSemanticFilterSettings = async (accessToken: string, settings: Record<string, any>) => {
  /**
   * Update MCP semantic filter settings
   * Settings will be applied across all pods within 10 seconds
   */
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/update/mcp_semantic_filter_settings`
      : `/update/mcp_semantic_filter_settings`;
    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(settings),
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update MCP semantic filter settings:", error);
    throw error;
  }
};

export const testMCPSemanticFilter = async (accessToken: string, model: string, query: string) => {
  /**
   * Test MCP semantic filter by making a responses API call
   * Returns both the response data and headers containing filter information
   */
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/v1/responses` : `/v1/responses`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: model,
        input: [
          {
            role: "user",
            content: query,
            type: "message",
          },
        ],
        tools: [
          {
            type: "mcp",
            server_url: "litellm_proxy",
            require_approval: "never",
          },
        ],
        tool_choice: "required",
      }),
    });

    // Extract headers before checking response status
    const filterHeader = response.headers.get("x-litellm-semantic-filter");
    const toolsHeader = response.headers.get("x-litellm-semantic-filter-tools");

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();

    // Return both data and headers
    return {
      data,
      headers: {
        filter: filterHeader,
        tools: toolsHeader,
      },
    };
  } catch (error) {
    console.error("Failed to test MCP semantic filter:", error);
    throw error;
  }
};

export const listMCPTools = async (
  accessToken: string,
  serverId: string,
  customHeaders?: Record<string, string>,
  includeDisabledTools?: boolean,
) => {
  // Construct base URL. include_disabled_tools returns the full server catalog
  // (admin-only, backend-enforced) so the settings UI can configure the allowlist.
  const query = `server_id=${serverId}${includeDisabledTools ? "&include_disabled_tools=true" : ""}`;
  let url = proxyBaseUrl ? `${proxyBaseUrl}/mcp-rest/tools/list?${query}` : `/mcp-rest/tools/list?${query}`;

  const headers: Record<string, string> = {
    [globalLitellmHeaderName]: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
    ...customHeaders, // Merge custom headers for passthrough auth
  };

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers,
    });
  } catch (error) {
    // Network-level failure (no HTTP response). Preserve legacy shape so the
    // caller can render a generic error message without crashing.
    console.error("Failed to fetch MCP tools (network error):", error);
    return {
      tools: [],
      error: "network_error",
      message: error instanceof Error ? error.message : "Failed to fetch MCP tools",
      stack_trace: null,
    };
  }

  let data: any = null;
  try {
    data = await response.json();
  } catch (parseError) {
    console.error("Failed to parse MCP tools response:", parseError);
    return {
      tools: [],
      error: "parse_error",
      message: "Failed to parse MCP tools response",
      status: response.status,
      statusText: response.statusText,
      stack_trace: null,
    };
  }

  if (!response.ok) {
    // Preserve the legacy "never throws" contract so existing callers
    // (e.g. MCPToolPermissions, MCPAppsPanel, MCPConnectPicker) can continue
    // to inspect `result.error` / `result.message`. Attach `status` so
    // callers that need to react to auth failures (e.g. the useQuery in
    // mcp_tools.tsx) can still detect 401s from the returned object.
    const errorMessage = (data && (data.message || data.error)) || "Failed to fetch MCP tools";
    return {
      tools: [],
      error: (data && data.error) || `http_${response.status}`,
      message: errorMessage,
      status: response.status,
      statusText: response.statusText,
      details: data,
      stack_trace: null,
    };
  }

  // Return the full response object which includes tools, error, message, and stack_trace
  return data;
};

interface CallMCPToolOptions {
  guardrails?: string[];
  customHeaders?: Record<string, string>;
}

export const callMCPTool = async (
  accessToken: string,
  serverId: string,
  toolName: string,
  toolArguments: Record<string, any>,
  options?: CallMCPToolOptions,
) => {
  try {
    // Construct base URL
    let url = proxyBaseUrl ? `${proxyBaseUrl}/mcp-rest/tools/call` : `/mcp-rest/tools/call`;

    const headers: Record<string, string> = {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(options?.customHeaders || {}), // Merge custom headers for passthrough auth
    };

    const body: Record<string, any> = {
      server_id: serverId,
      name: toolName,
      arguments: toolArguments,
    };
    if (options?.guardrails && options.guardrails.length > 0) {
      body.litellm_metadata = { guardrails: options.guardrails };
    }

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      let errorMessage = "Network response was not ok";
      let errorDetails = null;

      // First, try to get the response as text to see what we're dealing with
      const responseText = await response.text();

      try {
        // Try to parse as JSON
        const errorData = JSON.parse(responseText);

        if (errorData.detail) {
          if (typeof errorData.detail === "string") {
            errorMessage = errorData.detail;
          } else if (typeof errorData.detail === "object") {
            errorMessage = errorData.detail.message || errorData.detail.error || "An error occurred";
            errorDetails = errorData.detail;
          }
        } else {
          errorMessage = errorData.message || errorData.error || errorMessage;
        }
      } catch (parseError) {
        console.error("Failed to parse JSON error response:", parseError);
        // If JSON parsing fails, use the raw text
        if (responseText) {
          errorMessage = responseText;
        }
      }

      // Create a more informative error object
      const enhancedError = new Error(errorMessage);
      (enhancedError as any).status = response.status;
      (enhancedError as any).statusText = response.statusText;
      (enhancedError as any).details = errorDetails;

      handleError(errorMessage);
      throw enhancedError;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to call MCP tool:", error);
    console.error("Error type:", typeof error);
    if (error instanceof Error) {
      console.error("Error message:", error.message);
      console.error("Error stack:", error.stack);
    }
    throw error;
  }
};

export const testMCPToolsListRequest = async (
  accessToken: string | null,
  mcpServerConfig: Record<string, any>,
  oauthAccessToken?: string | null,
) => {
  try {
    // Construct the URL for POST request
    const url = proxyBaseUrl ? `${proxyBaseUrl}/mcp-rest/test/tools/list` : `/mcp-rest/test/tools/list`;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (accessToken) {
      headers["x-litellm-api-key"] = accessToken;
      if (globalLitellmHeaderName.toLowerCase() !== "authorization") {
        headers[globalLitellmHeaderName] = `Bearer ${accessToken}`;
      }
    }
    if (oauthAccessToken) {
      headers["Authorization"] = `Bearer ${oauthAccessToken}`;
    } else if (accessToken) {
      headers[globalLitellmHeaderName] = `Bearer ${accessToken}`;
    }

    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(mcpServerConfig),
    });

    // Check for non-JSON responses first
    const contentType = response.headers.get("content-type");
    if (!contentType || !contentType.includes("application/json")) {
      const text = await response.text();
      console.error("Received non-JSON response:", text);
      throw new Error(
        `Received non-JSON response (${response.status}: ${response.statusText}). Check network tab for details.`,
      );
    }

    const data = await response.json();

    if (!response.ok || data.error) {
      if (response.status === 403) {
        return {
          tools: [],
          error: true,
          status: 403,
          message: MCP_TOOLS_PREVIEW_FORBIDDEN_MESSAGE,
        };
      }
      // Return the error response instead of throwing an error
      // This allows the caller to handle the error format properly
      if (data.error) {
        return { ...data, status: response.status };
      }
      return {
        tools: [],
        error: "request_failed",
        status: response.status,
        message: data.message || `MCP tools list failed: ${response.status} ${response.statusText}`,
      };
    }

    return data;
  } catch (error) {
    console.error("MCP tools list test error:", error);
    // For network errors or other exceptions, still throw
    throw error;
  }
};
