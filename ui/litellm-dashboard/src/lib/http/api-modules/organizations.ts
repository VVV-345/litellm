// 组织管理接口；共用客户端状态，保持既有请求契约。
import type { ObjectPermission } from "@/components/object_permission_types";
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import type { Member } from "./teams";

export interface Organization {
  organization_id: string;
  organization_alias: string;
  budget_id: string;
  metadata: Record<string, any>;
  models: string[];
  spend: number;
  model_spend: Record<string, number>;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  litellm_budget_table: any; // Simplified to any since we don't need the detailed structure
  teams: any[] | null;
  users: any[] | null;
  members: any[] | null;
  object_permission?: ObjectPermission | null;
}

export const organizationListCall = async (
  accessToken: string,
  org_id: string | null = null,
  org_alias: string | null = null,
) => {
  /**
   * Get all organizations on proxy
   */
  try {
    return await apiClient.get(`/organization/list`, {
      accessToken,
      query: {
        org_id: org_id || undefined,
        org_alias: org_alias || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const organizationInfoCall = async (accessToken: string, organizationID: string) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/organization/info` : `/organization/info`;
    if (organizationID) {
      url = `${url}?organization_id=${organizationID}`;
    }
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
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const organizationUpdateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.patch(`/organization/update`, {
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

export const organizationDeleteCall = async (accessToken: string, organizationID: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/organization/delete` : `/organization/delete`;
    const response = await fetch(url, {
      method: "DELETE",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        organization_ids: [organizationID],
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(`Error deleting organization: ${errorData}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to delete organization:", error);
    throw error;
  }
};

export const organizationMemberAddCall = async (
  accessToken: string,
  organizationId: string,
  formValues: Member, // Assuming formValues is an object
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/organization/member_add` : `/organization/member_add`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        organization_id: organizationId,
        member: formValues, // Include formValues in the request body
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
    console.error("Failed to create organization member:", error);
    throw error;
  }
};

export const organizationMemberDeleteCall = async (accessToken: string, organizationId: string, userId: string) => {
  try {
    const data = await apiClient.delete(`/organization/member_delete`, {
      accessToken,
      body: {
        organization_id: organizationId,
        user_id: userId,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to delete organization member:", error);
    throw error;
  }
};

export const organizationMemberUpdateCall = async (
  accessToken: string,
  organizationId: string,
  formValues: Member, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.patch(`/organization/member_update`, {
      accessToken,
      body: {
        organization_id: organizationId,
        ...formValues, // Include formValues in the request body
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to update organization member:", error);
    throw error;
  }
};
