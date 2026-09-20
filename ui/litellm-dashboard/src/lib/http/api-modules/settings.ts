// 控制台配置接口；共用客户端状态，保持既有请求契约。
import {
  apiClient,
  defaultProxyBaseUrl,
  updateServerRootPath,
  updateProxyBaseUrl,
  proxyBaseUrl,
  globalLitellmHeaderName,
  handleError,
  getProxyBaseUrl,
} from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import { toast } from "@/lib/toast";

export const getCallbackConfigsCall = async (accessToken: string) => {
  /**
   * Get callback configuration metadata (logos, params, etc.)
   */
  try {
    return await apiClient.get(`/callbacks/configs`, { accessToken });
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

interface PublicModelHubInfo {
  docs_title: string;
  custom_docs_description: string | null;
  litellm_version: string;
  // Supports both old format (Record<string, string>) and new format (Record<string, {url: string, index: number}>)
  useful_links: Record<string, string | { url: string; index: number }>;
}

export interface WorkerInfo {
  worker_id: string;
  name: string;
  url: string;
}

export interface LiteLLMWellKnownUiConfig {
  server_root_path: string;
  proxy_base_url: string | null;
  auto_redirect_to_sso: boolean;
  admin_ui_disabled: boolean;
  sso_configured: boolean;
  hide_default_credentials_hint?: boolean;
  is_control_plane?: boolean;
  workers?: WorkerInfo[];
}

export const getUiConfig = async () => {
  /**Special route to get the proxy base url and server root path */
  const url = defaultProxyBaseUrl
    ? `${defaultProxyBaseUrl}/litellm/.well-known/litellm-ui-config`
    : `/litellm/.well-known/litellm-ui-config`;
  const response = await fetch(url);
  const jsonData: LiteLLMWellKnownUiConfig = await response.json();
  /**
   * Update the proxy base url and server root path
   */
  updateServerRootPath(jsonData.server_root_path);
  updateProxyBaseUrl(jsonData.server_root_path, jsonData.proxy_base_url);
  return jsonData;
};

export const getPublicModelHubInfo = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/model_hub/info` : `/public/model_hub/info`;
  const response = await fetch(url);
  const jsonData: PublicModelHubInfo = await response.json();
  return jsonData;
};

export const getOpenAPISchema = async () => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/openapi.json` : `/openapi.json`;
  const response = await fetch(url);
  const jsonData = await response.json();
  return jsonData;
};

export const alertingSettingsCall = async (accessToken: string) => {
  /**
   * Get all configurable params for setting a model
   */
  try {
    return await apiClient.get(`/alerting/settings`, { accessToken });
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

// Function to get allowed IPs
export const getAllowedIPs = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/get/allowed_ips`, { accessToken });
    return data.data; // Assuming the API returns { data: [...] }
  } catch (error) {
    console.error("Failed to get allowed IPs:", error);
    throw error;
  }
};

// Function to add an allowed IP
export const addAllowedIP = async (accessToken: string, ip: string) => {
  try {
    const data = await apiClient.post(`/add/allowed_ip`, { accessToken, body: { ip: ip } });
    return data;
  } catch (error) {
    console.error("Failed to add allowed IP:", error);
    throw error;
  }
};

// Function to delete an allowed IP
export const deleteAllowedIP = async (accessToken: string, ip: string) => {
  try {
    const data = await apiClient.post(`/delete/allowed_ip`, { accessToken, body: { ip: ip } });
    return data;
  } catch (error) {
    console.error("Failed to delete allowed IP:", error);
    throw error;
  }
};

export const updateUsefulLinksCall = async (
  accessToken: string,
  useful_links: Record<string, string | { url: string; index: number }>,
) => {
  try {
    return await apiClient.post(`/model_hub/update_useful_links`, {
      accessToken,
      body: { useful_links: useful_links },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const getCallbacksCall = async (accessToken: string, userID: string, userRole: string) => {
  /**
   * Get all the models user has access to
   */
  try {
    const data = await apiClient.get(`/get/config/callbacks`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const getGeneralSettingsCall = async (accessToken: string) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/config/list?config_type=general_settings`
      : `/config/list?config_type=general_settings`;

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
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const getRouterSettingsCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/router/settings`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get router settings:", error);
    throw error;
  }
};

export const getConfigFieldSetting = async (accessToken: string, fieldName: string) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/config/field/info?field_name=${fieldName}`
      : `/config/field/info?field_name=${fieldName}`;

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
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to set callbacks:", error);
    throw error;
  }
};

export const updateConfigFieldSetting = async (accessToken: string, fieldName: string, fieldValue: any) => {
  try {
    let formData = {
      field_name: fieldName,
      field_value: fieldValue,
      config_type: "general_settings",
    };
    const data = await apiClient.post(`/config/field/update`, { accessToken, body: formData });
    toast.success("Successfully updated value!");
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to set callbacks:", error);
    throw error;
  }
};

export const deleteConfigFieldSetting = async (accessToken: string, fieldName: string) => {
  try {
    let formData = {
      field_name: fieldName,
      config_type: "general_settings",
    };
    const data = await apiClient.post(`/config/field/delete`, { accessToken, body: formData });
    toast.success("Field reset on proxy");
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const setCallbacksCall = async (accessToken: string, formValues: Record<string, any>) => {
  /**
   * Set callbacks on proxy
   */
  try {
    const data = await apiClient.post(`/config/update`, {
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

export const getProxyUISettings = async (accessToken: string) => {
  /**
   * Get all the models user has access to
   */
  try {
    const data = await apiClient.get(`/sso/get/ui_settings`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};

export const getUISettings = async (accessToken: string) => {
  /**
   * Get UI-specific configuration flags from the database
   */
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/get/ui_settings` : `/get/ui_settings`;
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
      console.error("Failed to get UI settings:", errorMessage);
      return null;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get UI settings:", error);
    return null;
  }
};

export const deleteCallback = async (accessToken: string, callbackName: string) => {
  /**
   * Delete specific callback from proxy using the /config/callback/delete API
   */
  try {
    const data = await apiClient.post(`/config/callback/delete`, {
      accessToken,
      body: {
        callback_name: callbackName,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to delete specific callback:", error);
    throw error;
  }
};

export const getUiSettings = async () => {
  const proxyBaseUrl = getProxyBaseUrl();
  const url = proxyBaseUrl ? `${proxyBaseUrl}/get/ui_settings` : `/get/ui_settings`;
  const response = await fetch(url, {
    method: "GET",
  });
  if (!response.ok) {
    const errorData = await response.json();
    const errorMessage = deriveErrorMessage(errorData);
    throw new Error(errorMessage);
  }
  const data = await response.json();
  return data;
};

export const updateUiSettings = async (accessToken: string, settings: Record<string, any>) => {
  const proxyBaseUrl = getProxyBaseUrl();
  const url = proxyBaseUrl ? `${proxyBaseUrl}/update/ui_settings` : `/update/ui_settings`;
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
    throw new Error(errorMessage);
  }
  const data = await response.json();
  return data;
};

export type UserBannerSeverity = "info" | "warning" | "error";

export interface UserBanner {
  enabled: boolean;
  message: string;
  severity: UserBannerSeverity;
  revision: string;
}

export type UserBannerUpdate = Omit<UserBanner, "revision">;

export const getUserBanner = async (accessToken: string): Promise<UserBanner> => {
  return await apiClient.get<UserBanner>("/get/user_banner", { accessToken });
};

export const updateUserBanner = async (accessToken: string, banner: UserBannerUpdate): Promise<UserBanner> => {
  const data = await apiClient.patch<{ message: string; banner: UserBanner }>("/update/user_banner", {
    accessToken,
    body: banner,
  });
  return data.banner;
};
