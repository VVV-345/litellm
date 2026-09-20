// 健康检查接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export const serviceHealthCheck = async (accessToken: string, service: string) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/health/services?service=${service}`
      : `/health/services?service=${service}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      // throw error with message
      throw new Error(errorData);
    }

    const data = await response.json();
    // You can add additional logic here based on the response if needed
    return data;
  } catch (error) {
    console.error("Failed to perform health check:", error);
    throw error;
  }
};

export const individualModelHealthCheckCall = async (accessToken: string, modelId: string) => {
  /**
   * Run health check for a specific model using model ID (so each deployment is checked separately).
   */
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/health?model_id=${encodeURIComponent(modelId)}`
      : `/health?model_id=${encodeURIComponent(modelId)}`;

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
    console.error(`Failed to call /health for model id ${modelId}:`, error);
    throw error;
  }
};

export const cachingHealthCheckCall = async (accessToken: string) => {
  /**
   * Get all the models user has access to
   */
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/cache/ping` : `/cache/ping`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to call /cache/ping:", error);
    throw error;
  }
};

export const latestHealthChecksCall = async (accessToken: string) => {
  /**
   * Get the latest health check status for all models
   */
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/health/latest` : `/health/latest`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to call /health/latest:", error);
    throw error;
  }
};
