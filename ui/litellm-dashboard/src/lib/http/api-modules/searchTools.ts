// 搜索工具接口；共用客户端状态，保持既有请求契约。
import {
  apiClient,
  proxyBaseUrl,
  HTTP_REQUEST,
  globalLitellmHeaderName,
  handleError,
  getProxyBaseUrl,
} from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

// Search Tools API calls
export const fetchSearchTools = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/search_tools/list`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to fetch search tools:", error);
    throw error;
  }
};

export const createSearchTool = async (accessToken: string, formValues: Record<string, any>) => {
  try {
    const data = await apiClient.post(`/search_tools`, {
      accessToken,
      body: {
        search_tool: formValues,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to create search tool:", error);
    throw error;
  }
};

export const updateSearchTool = async (accessToken: string, searchToolId: string, formValues: Record<string, any>) => {
  try {
    const data = await apiClient.put(`/search_tools/${searchToolId}`, {
      accessToken,
      body: {
        search_tool: formValues,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to update search tool:", error);
    throw error;
  }
};

export const deleteSearchTool = async (accessToken: string, searchToolId: string) => {
  try {
    const data = await apiClient.delete(`/search_tools/${searchToolId}`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to delete search tool:", error);
    throw error;
  }
};

export const fetchAvailableSearchProviders = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/search_tools/ui/available_providers`
      : `/search_tools/ui/available_providers`;

    const response = await fetch(url, {
      method: HTTP_REQUEST.GET,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
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
    console.error("Failed to fetch available search providers:", error);
    throw error;
  }
};

export const testSearchToolConnection = async (accessToken: string, litellmParams: Record<string, any>) => {
  try {
    const data = await apiClient.post(`/search_tools/test_connection`, {
      accessToken,
      body: {
        litellm_params: litellmParams,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to test search tool connection:", error);
    throw error;
  }
};

export const searchToolQueryCall = async (
  accessToken: string,
  searchToolName: string,
  query: string,
  maxResults?: number,
): Promise<any> => {
  try {
    const url = `${getProxyBaseUrl()}/v1/search/${searchToolName}`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: query,
        max_results: maxResults || 5,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return null;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Error querying search tool:", error);
    throw error;
  }
};
