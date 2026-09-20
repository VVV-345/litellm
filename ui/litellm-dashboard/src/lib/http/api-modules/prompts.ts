// 提示词管理接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

interface PromptInfo {
  prompt_type: string;
  environment?: string;
}

export interface PromptSpec {
  prompt_id: string;
  litellm_params: object;
  prompt_info: PromptInfo;
  created_at?: string;
  updated_at?: string;
  version?: number; // Explicit version number for version history
  environment?: string;
  created_by?: string;
}

export interface PromptTemplateBase {
  litellm_prompt_id: string;
  content: string;
  metadata?: Record<string, unknown> | null;
}

interface PromptInfoResponse {
  prompt_spec: PromptSpec;
  raw_prompt_template: PromptTemplateBase | null;
  environments?: string[];
}

export interface ListPromptsResponse {
  prompts: PromptSpec[];
}

export const getPromptsList = async (accessToken: string, environment?: string): Promise<ListPromptsResponse> => {
  try {
    return await apiClient.get(`/prompts/list`, { accessToken, query: { environment: environment || undefined } });
  } catch (error) {
    console.error("Failed to get prompts list:", error);
    throw error;
  }
};

export const getPromptInfo = async (
  accessToken: string,
  promptId: string,
  environment?: string,
): Promise<PromptInfoResponse> => {
  try {
    return await apiClient.get(`/prompts/${promptId}/info`, {
      accessToken,
      query: { environment: environment || undefined },
    });
  } catch (error) {
    console.error("Failed to get prompt info:", error);
    throw error;
  }
};

export const getPromptVersions = async (
  accessToken: string,
  promptId: string,
  environment?: string,
): Promise<ListPromptsResponse> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/prompts/${promptId}/versions` : `/prompts/${promptId}/versions`;
    if (environment) {
      url += `?environment=${encodeURIComponent(environment)}`;
    }
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      // Don't throw global error for 404 (no versions found) as we might want to handle it gracefully
      if (response.status !== 404) {
        handleError(errorMessage);
      }
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get prompt versions:", error);
    throw error;
  }
};

export const createPromptCall = async (accessToken: string, promptData: any) => {
  try {
    const data = await apiClient.post(`/prompts`, { accessToken, body: promptData });
    return data;
  } catch (error) {
    console.error("Failed to create prompt:", error);
    throw error;
  }
};

export const updatePromptCall = async (accessToken: string, promptId: string, promptData: any) => {
  try {
    const data = await apiClient.put(`/prompts/${promptId}`, { accessToken, body: promptData });
    return data;
  } catch (error) {
    console.error("Failed to update prompt:", error);
    throw error;
  }
};

export const deletePromptCall = async (accessToken: string, promptId: string) => {
  try {
    const data = await apiClient.delete(`/prompts/${promptId}`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to delete prompt:", error);
    throw error;
  }
};

export const convertPromptFileToJson = async (
  accessToken: string,
  file: File,
): Promise<{ prompt_id: string; json_data: any }> => {
  try {
    const formData = new FormData();
    formData.append("file", file);

    const url = proxyBaseUrl ? `${proxyBaseUrl}/utils/dotprompt_json_converter` : `/utils/dotprompt_json_converter`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to convert prompt file:", error);
    throw error;
  }
};
