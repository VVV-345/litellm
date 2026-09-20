// 模型管理接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, apiClient, handleError } from "./clientState";
import { toast } from "@/lib/toast";
import { deriveErrorMessage } from "@/lib/http/client";

export interface Model {
  model_name: string;
  litellm_params: object;
  model_info: object | null;
}

export const makeModelGroupPublic = async (accessToken: string, modelGroups: string[]) => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/model_group/make_public` : `/model_group/make_public`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model_groups: modelGroups,
    }),
  });
  return response.json();
};

export const modelCreateCall = async (accessToken: string, formValues: Model) => {
  try {
    const data = await apiClient.post(`/model/new`, {
      accessToken,
      body: {
        ...formValues,
      },
    });

    // Close any existing messages before showing new ones
    toast.dismiss();

    // Sequential success messages
    toast.success(`Model ${formValues.model_name} created successfully`);

    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const modelDeleteCall = async (accessToken: string, model_id: string) => {
  try {
    const data = await apiClient.post(`/model/delete`, {
      accessToken,
      body: {
        id: model_id,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

let ModelListerrorShown = false;

let errorTimer: NodeJS.Timeout | null = null;

export const modelInfoCall = async (
  accessToken: string,
  userID: string,
  userRole: string,
  page: number = 1,
  size: number = 50,
  search?: string,
  modelId?: string,
  teamId?: string,
  sortBy?: string,
  sortOrder?: string,
  excludeAutoRouters?: boolean,
  modelName?: string,
) => {
  /**
   * Get all models on proxy
   */
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/v2/model/info` : `/v2/model/info`;
    const params = new URLSearchParams();
    params.append("include_team_models", "true");
    params.append("page", page.toString());
    params.append("size", size.toString());
    if (search && search.trim()) {
      params.append("search", search.trim());
    }
    if (modelName && modelName.trim()) {
      params.append("model", modelName.trim());
    }
    if (modelId && modelId.trim()) {
      params.append("modelId", modelId.trim());
    }
    if (teamId && teamId.trim()) {
      params.append("teamId", teamId.trim());
    }
    if (sortBy && sortBy.trim()) {
      params.append("sortBy", sortBy.trim());
    }
    if (sortOrder && sortOrder.trim()) {
      params.append("sortOrder", sortOrder.trim());
    }
    if (excludeAutoRouters) {
      params.append("exclude_auto_routers", "true");
    }
    if (params.toString()) {
      url += `?${params.toString()}`;
    }

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      let errorData = await response.text();
      errorData += `error shown=${ModelListerrorShown}`;
      if (!ModelListerrorShown) {
        if (errorData.includes("No model list passed")) {
          errorData = "No Models Exist. Click Add Model to get started.";
        }
        toast.info(errorData);
        ModelListerrorShown = true;

        if (errorTimer) clearTimeout(errorTimer);
        errorTimer = setTimeout(() => {
          ModelListerrorShown = false;
        }, 10000);
      }

      throw new Error("Network response was not ok");
    }

    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const modelInfoV1Call = async (accessToken: string, modelId: string) => {
  /**
   * Get all models on proxy
   */
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/v1/model/info` : `/v1/model/info`;
    url += `?litellm_model_id=${modelId}`;

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
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const modelHubPublicModelsCall = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/model_hub` : `/public/model_hub`;
  const response = await fetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    console.error(`modelHubPublicModelsCall failed with status ${response.status}`);
    return [];
  }
  return response.json();
};

export const modelHubCall = async (accessToken: string) => {
  /**
   * Get all models on proxy
   */
  try {
    const data = await apiClient.get(`/model_group/info`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const modelAvailableCall = async (
  accessToken: string,
  userID: string,
  userRole: string,
  return_wildcard_routes: boolean = false,
  teamID: string | null = null,
  include_model_access_groups: boolean = false,
  only_model_access_groups: boolean = false,
  scope?: string,
) => {
  /**
   * Get all the models user has access to
   */
  try {
    return await apiClient.get(`/models`, {
      accessToken,
      query: {
        include_model_access_groups: "True",
        return_wildcard_routes: return_wildcard_routes === true ? "True" : undefined,
        only_model_access_groups: only_model_access_groups === true ? "True" : undefined,
        team_id: teamID || undefined,
        scope: scope || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

/**
 * Patch update a model
 *
 * @param accessToken
 * @param formValues
 * @returns
 */
export const modelPatchUpdateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
  modelId: string,
) => {
  try {
    // Intentionally not logging the payload: it can contain freshly-entered
    // provider secrets (api_key, vertex_credentials, AWS creds).
    const url = proxyBaseUrl ? `${proxyBaseUrl}/model/${modelId}/update` : `/model/${modelId}/update`;
    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...formValues, // Include formValues in the request body
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      console.error("Error update from the server:", errorData);
      throw new Error("Network response was not ok");
    }
    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to update model:", error);
    throw error;
  }
};
