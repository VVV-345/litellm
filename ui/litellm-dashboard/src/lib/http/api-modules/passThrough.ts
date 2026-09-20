// 透传端点接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import { toast } from "@/lib/toast";

export const getPassThroughEndpointsCall = async (accessToken: string, teamId?: string | null) => {
  try {
    let path = `/config/pass_through_endpoint`;

    if (teamId) {
      path += `/team/${teamId}`;
    }

    const data = await apiClient.get(path, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const createPassThroughEndpoint = async (accessToken: string, formValues: Record<string, any>) => {
  /**
   * Set callbacks on proxy
   */
  try {
    const data = await apiClient.post(`/config/pass_through_endpoint`, {
      accessToken,
      body: {
        ...formValues, // Include formValues in the request body
      },
    });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to set callbacks:", error);
    throw error;
  }
};

export const deletePassThroughEndpointsCall = async (accessToken: string, endpointId: string) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/config/pass_through_endpoint?endpoint_id=${endpointId}`
      : `/config/pass_through_endpoint?endpoint_id=${endpointId}`;

    const response = await fetch(url, {
      method: "DELETE",
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
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const updatePassThroughEndpoint = async (
  accessToken: string,
  endpointPath: string,
  formValues: Record<string, any>,
) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/config/pass_through_endpoint/${encodeURIComponent(endpointPath)}`
      : `/config/pass_through_endpoint/${encodeURIComponent(endpointPath)}`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(formValues),
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    toast.success("Pass through endpoint updated successfully");
    return data;
  } catch (error) {
    console.error("Failed to update pass through endpoint:", error);
    throw error;
  }
};
