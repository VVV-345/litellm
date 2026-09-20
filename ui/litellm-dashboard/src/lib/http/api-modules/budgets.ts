// 预算管理接口；共用客户端状态，保持既有请求契约。
import { apiClient } from "./clientState";

export const budgetDeleteCall = async (accessToken: string | null, budget_id: string) => {
  if (accessToken == null) {
    return;
  }

  try {
    const data = await apiClient.post(`/budget/delete`, {
      accessToken,
      body: {
        id: budget_id,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const budgetCreateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.post(`/budget/new`, {
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

export const budgetUpdateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.post(`/budget/update`, {
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

export const getBudgetList = async (accessToken: string) => {
  /**
   * Get all configurable params for setting a budget
   */
  try {
    const data = await apiClient.get(`/budget/list`, { accessToken });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to get callbacks:", error);
    throw error;
  }
};
