// 供应商凭据接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, apiClient } from "./clientState";

export interface CredentialItem {
  credential_name: string;
  credential_values: any;
  credential_info: {
    custom_llm_provider?: string;
    description?: string;
    required?: boolean;
  };
}

export interface ProviderCredentialFieldMetadata {
  key: string;
  label: string;
  placeholder?: string | null;
  tooltip?: string | null;
  required?: boolean;
  field_type?: "text" | "password" | "select" | "upload" | "textarea";
  options?: string[] | null;
  default_value?: string | null;
}

export interface ProviderCreateInfo {
  provider: string;
  provider_display_name: string;
  litellm_provider: string;
  default_model_placeholder?: string | null;
  credential_fields: ProviderCredentialFieldMetadata[];
}

export interface CredentialsResponse {
  credentials: CredentialItem[];
}

export const getProviderCreateMetadata = async (): Promise<ProviderCreateInfo[]> => {
  /**
   * Fetch provider credential field metadata from the proxy's public endpoint.
   * This is used by the UI to dynamically render provider-specific credential fields.
   */
  const url = proxyBaseUrl ? `${proxyBaseUrl}/public/providers/fields` : `/public/providers/fields`;
  const response = await fetch(url, {
    method: "GET",
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error("Failed to fetch provider create metadata:", response.status, errorText);
    throw new Error("Failed to load provider configuration");
  }

  const jsonData: ProviderCreateInfo[] = await response.json();
  return jsonData;
};

export const credentialCreateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    if (formValues.metadata) {
      // if there's an exception JSON.parse, show it in the message
      try {
        formValues.metadata = JSON.parse(formValues.metadata);
      } catch (error) {
        throw new Error("Failed to parse metadata: " + error);
      }
    }

    const data = await apiClient.post(`/credentials`, {
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

export const credentialListCall = async (accessToken: string) => {
  /**
   * Get all available teams on proxy
   */
  try {
    const data = await apiClient.get(`/credentials`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const credentialGetCall = async (accessToken: string, credentialName: string | null, modelId: string | null) => {
  try {
    let path = `/credentials`;

    if (credentialName) {
      path += `/by_name/${credentialName}`;
    } else if (modelId) {
      path += `/by_model/${modelId}`;
    }

    const data = await apiClient.get(path, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const credentialDeleteCall = async (accessToken: string, credentialName: string) => {
  try {
    const data = await apiClient.delete(`/credentials/${credentialName}`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to delete key:", error);
    throw error;
  }
};

export const credentialUpdateCall = async (
  accessToken: string,
  credentialName: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    if (formValues.metadata) {
      // if there's an exception JSON.parse, show it in the message
      try {
        formValues.metadata = JSON.parse(formValues.metadata);
      } catch (error) {
        throw new Error("Failed to parse metadata: " + error);
      }
    }

    const data = await apiClient.patch(`/credentials/${credentialName}`, {
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
