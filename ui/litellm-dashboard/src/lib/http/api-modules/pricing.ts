// 模型价格表接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName } from "./clientState";

export const modelCostMap = async () => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/public/litellm_model_cost_map` : `/public/litellm_model_cost_map`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    });
    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to get model cost map:", error);
    throw error;
  }
};

export const reloadModelCostMap = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/reload/model_cost_map` : `/reload/model_cost_map`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to reload model cost map:", error);
    throw error;
  }
};

export const scheduleModelCostMapReload = async (accessToken: string, hours: number) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/schedule/model_cost_map_reload?hours=${hours}`
      : `/schedule/model_cost_map_reload?hours=${hours}`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to schedule model cost map reload:", error);
    throw error;
  }
};

export const cancelModelCostMapReload = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/schedule/model_cost_map_reload` : `/schedule/model_cost_map_reload`;
    const response = await fetch(url, {
      method: "DELETE",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to cancel model cost map reload:", error);
    throw error;
  }
};

export const getModelCostMapSource = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/model/cost_map/source` : `/model/cost_map/source`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to get model cost map source info:", error);
    throw error;
  }
};

export const getModelCostMapReloadStatus = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/schedule/model_cost_map_reload/status`
      : `/schedule/model_cost_map_reload/status`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      console.error(`Status request failed with status: ${response.status}`);
      const errorText = await response.text();
      console.error("Error response:", errorText);
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const jsonData = await response.json();
    return jsonData;
  } catch (error) {
    console.error("Failed to get model cost map reload status:", error);
    throw error;
  }
};
