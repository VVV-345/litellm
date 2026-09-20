// 插件市场接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, getProxyBaseUrl, handleError, globalLitellmHeaderName } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import type { SkillRegisterRequest } from "@/components/claude_code_plugins/types";

export const skillHubPublicCall = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/skill_hub` : `/public/skill_hub`;
  const response = await fetch(url, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    console.error(`skillHubPublicCall failed with status ${response.status}`);
    return { plugins: [] };
  }
  return response.json();
};

// Claude Code Marketplace Networking Functions

/**
 * Get public marketplace catalog (no authentication required)
 * Returns marketplace.json for Claude Code CLI discovery
 */
export const getClaudeCodeMarketplace = async () => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl ? `${proxyBaseUrl}/claude-code/marketplace.json` : `/claude-code/marketplace.json`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch Claude Code marketplace:", error);
    throw error;
  }
};

/**
 * List all Claude Code plugins (admin only)
 * @param accessToken - Admin access token
 * @param enabledOnly - If true, only return enabled plugins (default: false)
 */
export const getClaudeCodePluginsList = async (accessToken: string, enabledOnly: boolean = false) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/claude-code/plugins?enabled_only=${enabledOnly}`
      : `/claude-code/plugins?enabled_only=${enabledOnly}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch Claude Code plugins list:", error);
    throw error;
  }
};

/**
 * Get details for a specific Claude Code plugin (admin only)
 * @param accessToken - Admin access token
 * @param pluginName - Name of the plugin
 */
export const getClaudeCodePluginDetails = async (accessToken: string, pluginName: string) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/claude-code/plugins/${pluginName}`
      : `/claude-code/plugins/${pluginName}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error(`Failed to fetch plugin "${pluginName}":`, error);
    throw error;
  }
};

/**
 * Register a new Claude Code plugin (admin only). Create-only: the proxy returns
 * 409 if a plugin with the same name already exists.
 * @param accessToken - Admin access token
 * @param pluginData - Plugin registration data
 */
export const registerClaudeCodePlugin = async (accessToken: string, pluginData: SkillRegisterRequest) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl ? `${proxyBaseUrl}/claude-code/plugins` : `/claude-code/plugins`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(pluginData),
    });

    if (!response.ok) {
      const errorBody = await response.text();
      let errorMessage: string;
      try {
        errorMessage = deriveErrorMessage(JSON.parse(errorBody));
      } catch {
        errorMessage = errorBody || `Request failed with status ${response.status}`;
      }
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to register Claude Code plugin:", error);
    throw error;
  }
};

/**
 * Enable a Claude Code plugin (admin only)
 * @param accessToken - Admin access token
 * @param pluginName - Name of the plugin to enable
 */
export const enableClaudeCodePlugin = async (accessToken: string, pluginName: string) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/claude-code/plugins/${pluginName}/enable`
      : `/claude-code/plugins/${pluginName}/enable`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error(`Failed to enable plugin "${pluginName}":`, error);
    throw error;
  }
};

/**
 * Disable a Claude Code plugin (admin only)
 * @param accessToken - Admin access token
 * @param pluginName - Name of the plugin to disable
 */
export const disableClaudeCodePlugin = async (accessToken: string, pluginName: string) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/claude-code/plugins/${pluginName}/disable`
      : `/claude-code/plugins/${pluginName}/disable`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error(`Failed to disable plugin "${pluginName}":`, error);
    throw error;
  }
};

/**
 * Delete a Claude Code plugin (admin only)
 * @param accessToken - Admin access token
 * @param pluginName - Name of the plugin to delete
 */
export const deleteClaudeCodePlugin = async (accessToken: string, pluginName: string) => {
  try {
    const proxyBaseUrl = getProxyBaseUrl();
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/claude-code/plugins/${pluginName}`
      : `/claude-code/plugins/${pluginName}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      const errorMessage = deriveErrorMessage(JSON.parse(errorData));
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error(`Failed to delete plugin "${pluginName}":`, error);
    throw error;
  }
};
