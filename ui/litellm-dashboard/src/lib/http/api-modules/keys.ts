// 虚拟密钥接口；共用客户端状态，保持既有请求契约。
import { jsonFields } from "@/components/common_components/check_openapi_schema";
import { proxyBaseUrl, globalLitellmHeaderName, handleError, apiClient } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import { toast } from "@/lib/toast";

export const keyCreateServiceAccountCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    // check if formValues.description is not undefined, make it a string and add it to formValues.metadata
    if (formValues.description) {
      // add to formValues.metadata
      if (!formValues.metadata) {
        formValues.metadata = {};
      }
      // value needs to be in "", valid JSON
      formValues.metadata.description = formValues.description;
      // remove descrption from formValues
      delete formValues.description;
      formValues.metadata = JSON.stringify(formValues.metadata);
    }
    // Parse JSON fields if they exist
    for (const field of jsonFields) {
      if (formValues[field]) {
        // if there's an exception JSON.parse, show it in the message
        try {
          formValues[field] = JSON.parse(formValues[field]);
        } catch (error) {
          throw new Error(`Failed to parse ${field}: ` + error);
        }
      }
    }

    const url = proxyBaseUrl ? `${proxyBaseUrl}/key/service-account/generate` : `/key/service-account/generate`;
    const response = await fetch(url, {
      method: "POST",
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
      console.error("Error response from the server:", errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const keyCreateCall = async (
  accessToken: string,
  userID: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    // check if formValues.description is not undefined, make it a string and add it to formValues.metadata
    if (formValues.description) {
      // add to formValues.metadat
      if (!formValues.metadata) {
        formValues.metadata = {};
      }
      // value needs to be in "", valid JSON
      formValues.metadata.description = formValues.description;
      // remove descrption from formValues
      delete formValues.description;
      formValues.metadata = JSON.stringify(formValues.metadata);
    }
    // Parse JSON fields if they exist
    for (const field of jsonFields) {
      if (formValues[field]) {
        // if there's an exception JSON.parse, show it in the message
        try {
          formValues[field] = JSON.parse(formValues[field]);
        } catch (error) {
          throw new Error(`Failed to parse ${field}: ` + error);
        }
      }
    }

    const url = proxyBaseUrl ? `${proxyBaseUrl}/key/generate` : `/key/generate`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        user_id: userID,
        ...formValues, // Include formValues in the request body
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      console.error("Error response from the server:", errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const keyCreateForAgentCall = async (
  accessToken: string,
  agentId: string,
  keyAlias: string,
  models: string[],
  metadata?: Record<string, any>,
  teamId?: string | null,
) => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/key/generate` : `/key/generate`;
  const body: Record<string, any> = {
    agent_id: agentId,
    key_alias: keyAlias,
    models: models.length > 0 ? models : [],
  };
  if (teamId) {
    body.team_id = teamId;
  }
  if (metadata && Object.keys(metadata).length > 0) {
    body.metadata = metadata;
  }
  const response = await fetch(url, {
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
    throw new Error("Failed to create key for agent");
  }

  return response.json();
};

export const keyDeleteCall = async (accessToken: string, user_key: string) => {
  try {
    return await apiClient.post(`/key/delete`, { accessToken, body: { keys: [user_key] } });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const regenerateKeyCall = async (accessToken: string, keyToRegenerate: string, formData: any) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/key/${keyToRegenerate}/regenerate`
      : `/key/${keyToRegenerate}/regenerate`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(formData),
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
    console.error("Failed to regenerate key:", error);
    throw error;
  }
};

export const keyInfoCall = async (accessToken: string, keys: string[]) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/v2/key/info` : `/v2/key/info`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        keys: keys,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      if (errorData.includes("Invalid proxy server token passed")) {
        throw new Error("Invalid proxy server token passed");
      }
      handleError(errorData);
      throw new Error("Network response was not ok");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

// ... existing code ...
export const keyInfoV1Call = async (accessToken: string, key: string) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/key/info` : `/key/info`;
    url = `${url}?key=${key}`; // Add key as query parameter

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      // Remove body since this is a GET request
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      toast.fromError("Failed to fetch key info - " + errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch key info:", error);
    throw error;
  }
};

export const keyListCall = async (
  accessToken: string,
  organizationID: string | null,
  teamID: string | null,
  selectedKeyAlias: string | null,
  userID: string | null,
  keyHash: string | null,
  page: number,
  pageSize: number,
  sortBy: string | null = null,
  sortOrder: string | null = null,
  expand: string | null = null,
  status: string | null = null,
) => {
  /**
   * Get all available teams on proxy
   */
  try {
    return await apiClient.get(`/key/list`, {
      accessToken,
      query: {
        team_id: teamID || undefined,
        organization_id: organizationID || undefined,
        key_alias: selectedKeyAlias || undefined,
        key_hash: keyHash || undefined,
        user_id: userID || undefined,
        page: page ? page.toString() : undefined,
        size: pageSize ? pageSize.toString() : undefined,
        sort_by: sortBy || undefined,
        sort_order: sortOrder || undefined,
        expand: expand || undefined,
        status: status || undefined,
        return_full_object: "true",
        include_team_keys: "true",
        include_created_by_keys: "true",
        // /key/list is exact by default; opt in so the key-list search box keeps
        // matching partial user_id/key_alias.
        substring_matching: "true",
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export interface PaginatedKeyAliasResponse {
  aliases: string[];
  total_count: number;
  current_page: number;
  total_pages: number;
  size: number;
}

export const keyAliasesCall = async (
  accessToken: string,
  page: number = 1,
  size: number = 50,
  search?: string,
  team_id?: string,
): Promise<PaginatedKeyAliasResponse> => {
  /**
   * Get key aliases from proxy with pagination and optional search
   */
  try {
    return await apiClient.get(`/key/aliases`, {
      accessToken,
      query: {
        page: String(page),
        size: String(size),
        search: search || undefined,
        team_id: team_id || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch key aliases:", error);
    throw error;
  }
};

export const keyUpdateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    if (formValues.model_tpm_limit) {
      // if there's an exception JSON.parse, show it in the message
      try {
        formValues.model_tpm_limit = JSON.parse(formValues.model_tpm_limit);
      } catch (error) {
        throw new Error("Failed to parse model_tpm_limit: " + error);
      }
    }

    if (formValues.model_rpm_limit) {
      // if there's an exception JSON.parse, show it in the message
      try {
        formValues.model_rpm_limit = JSON.parse(formValues.model_rpm_limit);
      } catch (error) {
        throw new Error("Failed to parse model_rpm_limit: " + error);
      }
    }
    const url = proxyBaseUrl ? `${proxyBaseUrl}/key/update` : `/key/update`;
    const response = await fetch(url, {
      method: "POST",
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
      console.error("Error response from the server:", errorData);
      throw new Error(errorData);
    }
    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};
