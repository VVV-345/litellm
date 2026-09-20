// 用户管理接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import type { ObjectPermission } from "@/components/object_permission_types";
import type { ModelMaxBudget, ModelBudgetUsage } from "@/components/key_team_helpers/ModelMaxBudgetEditor";

export const invitationCreateCall = async (
  accessToken: string,
  userID: string, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.post(`/invitation/new`, {
      accessToken,
      body: {
        user_id: userID, // Include formValues in the request body
      },
    });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const userCreateCall = async (
  accessToken: string,
  userID: string | null,
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

    formValues.auto_create_key = false;
    // if formValues.metadata is not undefined, make it a valid dict
    if (formValues.metadata) {
      // if there's an exception JSON.parse, show it in the message
      try {
        formValues.metadata = JSON.parse(formValues.metadata);
      } catch (error) {
        throw new Error("Failed to parse metadata: " + error);
      }
    }

    const url = proxyBaseUrl ? `${proxyBaseUrl}/user/new` : `/user/new`;
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

export const userDeleteCall = async (accessToken: string, userIds: string[]) => {
  try {
    return await apiClient.post(`/user/delete`, { accessToken, body: { user_ids: userIds } });
  } catch (error) {
    console.error("Failed to delete user(s):", error);
    throw error;
  }
};

export interface UserInfo {
  user_id: string;
  user_email: string;
  user_alias: string | null;
  user_role: string;
  spend: number;
  max_budget: number | null;
  models: string[];
  key_count: number;
  created_at: string;
  updated_at: string;
  sso_user_id: string | null;
  budget_duration: string | null;
  metadata?: Record<string, unknown> | null;
}

export type UserListResponse = {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  users: UserInfo[];
};

export const userListCall = async (
  accessToken: string,
  userIDs: string[] | null = null,
  page: number | null = null,
  page_size: number | null = null,
  userEmail: string | null = null,
  userRole: string | null = null,
  team: string | null = null,
  sso_user_id: string | null = null,
  sortBy: string | null = null,
  sortOrder: "asc" | "desc" | null = null,
  organizationIds: string[] | null = null,
) => {
  /**
   * Get all available teams on proxy
   */
  try {
    const data = (await apiClient.get(`/user/list`, {
      accessToken,
      query: {
        user_ids: userIDs && userIDs.length > 0 ? userIDs.join(",") : undefined,
        page: page || undefined,
        page_size: page_size || undefined,
        user_email: userEmail || undefined,
        role: userRole || undefined,
        team: team || undefined,
        sso_user_ids: sso_user_id || undefined,
        sort_by: sortBy || undefined,
        sort_order: sortOrder || undefined,
        organization_ids: organizationIds && organizationIds.length > 0 ? organizationIds.join(",") : undefined,
      },
    })) as UserListResponse;
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

/**
 * Response type for /v2/user/info — lightweight endpoint that returns only the user object.
 */
export interface UserInfoV2Response {
  user_id: string;
  user_email: string | null;
  user_alias: string | null;
  user_role: string | null;
  spend: number;
  max_budget: number | null;
  models: string[];
  budget_duration: string | null;
  budget_reset_at: string | null;
  metadata: Record<string, any> | null;
  created_at: string | null;
  updated_at: string | null;
  sso_user_id: string | null;
  teams: string[];
  object_permission?: ObjectPermission | null;
  model_max_budget?: ModelMaxBudget | null;
  model_max_budget_usage?: Record<string, ModelBudgetUsage> | null;
}

/**
 * Lightweight user info fetch from /v2/user/info.
 * Returns only the user object — no keys, no teams objects.
 *
 * @param accessToken - Bearer token for auth
 * @param userId - Optional user ID to look up. If omitted, returns the caller's own info.
 */
export const userGetInfoV2 = async (accessToken: string, userId?: string): Promise<UserInfoV2Response> => {
  try {
    return await apiClient.get(`/v2/user/info`, { accessToken, query: { user_id: userId || undefined } });
  } catch (error) {
    console.error("Failed to fetch user info v2:", error);
    throw error;
  }
};

export const userInfoCall = async (
  accessToken: string,
  userID: string | null,
  userRole: string,
  viewAll: boolean = false,
  page: number | null,
  page_size: number | null,
  lookup_user_id: boolean = false,
) => {
  try {
    if (viewAll) {
      return await apiClient.get(`/user/list`, {
        accessToken,
        query: {
          page: page != null ? page.toString() : undefined,
          page_size: page_size != null ? page_size.toString() : undefined,
        },
      });
    }

    const includeUserID = !((userRole === "Admin" || userRole === "Admin Viewer") && !lookup_user_id) && userID;
    return await apiClient.get(`/user/info`, {
      accessToken,
      query: { user_id: includeUserID ? userID : undefined },
    });
  } catch (error) {
    console.error("Failed to fetch user data:", error);
    throw error;
  }
};

export const getPossibleUserRoles = async (accessToken: string) => {
  try {
    const data = (await apiClient.get(`/user/available_roles`, { accessToken })) as Record<
      string,
      Record<string, string>
    >;
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    throw error;
  }
};

export const userUpdateUserCall = async (
  accessToken: string,
  formValues: any, // Assuming formValues is an object
  userRole: string | null,
) => {
  try {
    const response_body = { ...formValues };
    if (userRole !== null) {
      response_body["user_role"] = userRole;
    }
    const data = (await apiClient.post(`/user/update`, { accessToken, body: response_body })) as {
      user_id: string;
      data: UserInfo;
    };
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const userBulkUpdateUserCall = async (
  accessToken: string,
  formValues: any, // Assuming formValues is an object
  userIds?: string[], // Optional - if not provided, will update all users
  allUsers: boolean = false, // Flag to update all users
) => {
  try {
    let request_body: Record<string, any>;

    if (allUsers) {
      // Update all users mode
      request_body = {
        all_users: true,
        user_updates: formValues,
      };
    } else if (userIds && userIds.length > 0) {
      // Update specific users mode
      let users = [];
      for (const user_id of userIds) {
        users.push({
          user_id: user_id,
          ...formValues,
        });
      }
      request_body = {
        users: users,
      };
    } else {
      throw new Error("Must provide either userIds or set allUsers=true");
    }

    const data = (await apiClient.post(`/user/bulk_update`, { accessToken, body: request_body })) as {
      results: Array<{
        user_id?: string;
        user_email?: string;
        success: boolean;
        error?: string;
        updated_user?: any;
      }>;
      total_requested: number;
      successful_updates: number;
      failed_updates: number;
    };
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};
