// 向量存储接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, apiClient, getProxyBaseUrl, handleError } from "./clientState";
import type { VectorStoreIndex } from "@/app/(dashboard)/vector-stores/_components/IndexesTab";

export const vectorStoreCreateCall = async (accessToken: string, formValues: Record<string, any>): Promise<void> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/vector_store/new` : `/vector_store/new`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(formValues),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to create vector store");
    }

    return await response.json();
  } catch (error) {
    console.error("Error creating vector store:", error);
    throw error;
  }
};

export const vectorStoreListCall = async (
  accessToken: string,
  page: number = 1,
  page_size: number = 100,
): Promise<any> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/vector_store/list` : `/vector_store/list`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to list vector stores");
    }

    return await response.json();
  } catch (error) {
    console.error("Error listing vector stores:", error);
    throw error;
  }
};

export interface IndexesListResponse {
  object: string;
  data: VectorStoreIndex[];
}

export const indexesListCall = async (accessToken: string): Promise<IndexesListResponse> => {
  try {
    return await apiClient.get<IndexesListResponse>(`/v1/indexes`, { accessToken });
  } catch (error) {
    console.error("Error listing indexes:", error);
    throw error;
  }
};

export const vectorStoreDeleteCall = async (accessToken: string, vectorStoreId: string): Promise<void> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/vector_store/delete` : `/vector_store/delete`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ vector_store_id: vectorStoreId }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to delete vector store");
    }

    return await response.json();
  } catch (error) {
    console.error("Error deleting vector store:", error);
    throw error;
  }
};

export const vectorStoreInfoCall = async (accessToken: string, vectorStoreId: string): Promise<any> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/vector_store/info` : `/vector_store/info`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ vector_store_id: vectorStoreId }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to get vector store info");
    }

    return await response.json();
  } catch (error) {
    console.error("Error getting vector store info:", error);
    throw error;
  }
};

export const vectorStoreUpdateCall = async (accessToken: string, formValues: Record<string, any>): Promise<any> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/vector_store/update` : `/vector_store/update`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(formValues),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to update vector store");
    }

    return await response.json();
  } catch (error) {
    console.error("Error updating vector store:", error);
    throw error;
  }
};

export const ragIngestCall = async (
  accessToken: string,
  file: File,
  customLlmProvider: string,
  vectorStoreId?: string,
  vectorStoreName?: string,
  vectorStoreDescription?: string,
  providerSpecificParams?: Record<string, any>,
): Promise<any> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/rag/ingest` : `/rag/ingest`;

    const formData = new FormData();
    formData.append("file", file);

    const ingestOptions: any = {
      ingest_options: {
        vector_store: {
          custom_llm_provider: customLlmProvider,
          ...(vectorStoreId && { vector_store_id: vectorStoreId }),
          ...(providerSpecificParams && providerSpecificParams),
        },
      },
    };

    // Add litellm_vector_store_params if name or description provided
    if (vectorStoreName || vectorStoreDescription) {
      ingestOptions.ingest_options.litellm_vector_store_params = {};
      if (vectorStoreName) {
        ingestOptions.ingest_options.litellm_vector_store_params.vector_store_name = vectorStoreName;
      }
      if (vectorStoreDescription) {
        ingestOptions.ingest_options.litellm_vector_store_params.vector_store_description = vectorStoreDescription;
      }
    }

    formData.append("request", JSON.stringify(ingestOptions));

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error?.message || error.detail || "Failed to ingest document");
    }

    return await response.json();
  } catch (error) {
    console.error("Error ingesting document:", error);
    throw error;
  }
};

export const vectorStoreSearchCall = async (
  accessToken: string,
  vectorStoreId: string,
  query: string,
): Promise<any> => {
  try {
    const url = `${getProxyBaseUrl()}/v1/vector_stores/${vectorStoreId}/search`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: query,
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
    console.error("Error testing vector store search:", error);
    throw error;
  }
};
