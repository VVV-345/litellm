// 团队管理接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { Team } from "@/components/key_team_helpers/key_list";
import { toast } from "@/lib/toast";
import { unwrapProxyErrorMessage, deriveErrorMessage } from "@/lib/http/client";

export const teamDeleteCall = async (accessToken: string, teamID: string) => {
  try {
    return await apiClient.post(`/team/delete`, { accessToken, body: { team_ids: [teamID] } });
  } catch (error) {
    console.error("Failed to delete key:", error);
    throw error;
  }
};

export const teamInfoCall = async (accessToken: string, teamID: string | null) => {
  try {
    return await apiClient.get(`/team/info`, { accessToken, query: { team_id: teamID || undefined } });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

type TeamListResponse = {
  teams: Team[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export const v2TeamListCall = async (
  accessToken: string,
  organizationID: string | null,
  userID: string | null = null,
  teamID: string | null = null,
  team_alias: string | null = null,
  page: number = 1,
  page_size: number = 10,
  sort_by: string | null = null,
  sort_order: "asc" | "desc" | null = null,
): Promise<TeamListResponse> => {
  /**
   * Get list of teams with filtering and sorting options
   */
  try {
    return await apiClient.get(`/v2/team/list`, {
      accessToken,
      query: {
        user_id: userID || undefined,
        organization_id: organizationID || undefined,
        team_id: teamID || undefined,
        team_alias: team_alias || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const teamListCall = async (
  accessToken: string,
  organizationID: string | null,
  userID: string | null = null,
  teamID: string | null = null,
  team_alias: string | null = null,
) => {
  /**
   * Get all available teams on proxy
   */
  try {
    return await apiClient.get(`/team/list`, {
      accessToken,
      query: {
        user_id: userID || undefined,
        organization_id: organizationID || undefined,
        team_id: teamID || undefined,
        team_alias: team_alias || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const availableTeamListCall = async (accessToken: string) => {
  /**
   * Get all available teams on proxy
   */
  try {
    const data = await apiClient.get(`/team/available`, { accessToken });
    return data;
  } catch (error) {
    throw error;
  }
};

export const teamCreateCall = async (
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

    const data = await apiClient.post(`/team/new`, {
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

export const teamUpdateCall = async (
  accessToken: string,
  formValues: Record<string, any>, // Assuming formValues is an object
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/team/update` : `/team/update`;
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
      toast.fromError("Failed to update team settings: " + unwrapProxyErrorMessage(errorData));
      throw new Error(errorData);
    }
    const data = (await response.json()) as { data: Team; team_id: string };
    return data;
    // Handle success - you might want to update some state or UI based on the updated team
  } catch (error) {
    console.error("Failed to update team:", error);
    throw error;
  }
};

export interface Member {
  role: string;
  user_id: string | null;
  user_email?: string | null;
  max_budget_in_team?: number | null;
  tpm_limit?: number | null;
  rpm_limit?: number | null;
  budget_duration?: string | null;
  allowed_models?: string[] | null;
}

export const teamMemberAddCall = async (accessToken: string, teamId: string, formValues: Member) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/team/member_add` : `/team/member_add`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        team_id: teamId,
        member: formValues,
      }),
    });

    if (!response.ok) {
      // Read and parse JSON error body
      const errorText = await response.text();
      let parsedError: any = {};

      try {
        parsedError = JSON.parse(errorText);
      } catch (e) {
        console.warn("Failed to parse error body as JSON:", errorText);
      }

      const rawMessage = parsedError?.detail?.error || "Failed to add team member";
      const err = new Error(rawMessage);
      (err as any).raw = parsedError;
      throw err;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const teamBulkMemberAddCall = async (
  accessToken: string,
  teamId: string,
  members: Member[] | null,
  maxBudgetInTeam?: number,
  allUsers?: boolean,
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/team/bulk_member_add` : `/team/bulk_member_add`;

    let requestBody: any = {
      team_id: teamId,
    };

    if (allUsers) {
      requestBody.all_users = true;
    } else {
      requestBody.members = members;
    }

    if (maxBudgetInTeam !== undefined && maxBudgetInTeam !== null) {
      requestBody.max_budget_in_team = maxBudgetInTeam;
    }

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      // Read and parse JSON error body
      const errorText = await response.text();
      let parsedError: any = {};

      try {
        parsedError = JSON.parse(errorText);
      } catch (e) {
        console.warn("Failed to parse error body as JSON:", errorText);
      }

      const rawMessage = parsedError?.detail?.error || "Failed to bulk add team members";
      const err = new Error(rawMessage);
      (err as any).raw = parsedError;
      throw err;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to bulk add team members:", error);
    throw error;
  }
};

export const teamMemberUpdateCall = async (
  accessToken: string,
  teamId: string,
  formValues: Member, // Assuming formValues is an object
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/team/member_update` : `/team/member_update`;

    const requestBody: any = {
      team_id: teamId,
      role: formValues.role,
      user_id: formValues.user_id,
    };

    const orNull = (value: unknown) => (value === undefined || value === null || value === "" ? null : value);
    if (formValues.user_email !== undefined) {
      requestBody.user_email = formValues.user_email;
    }
    if ("max_budget_in_team" in formValues) {
      requestBody.max_budget_in_team = orNull(formValues.max_budget_in_team);
    }
    if ("tpm_limit" in formValues) {
      requestBody.tpm_limit = orNull(formValues.tpm_limit);
    }
    if ("rpm_limit" in formValues) {
      requestBody.rpm_limit = orNull(formValues.rpm_limit);
    }
    if ("budget_duration" in formValues) {
      requestBody.budget_duration = orNull(formValues.budget_duration);
    }
    if (formValues.allowed_models !== undefined) {
      requestBody.allowed_models = formValues.allowed_models;
    }

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      // Read and parse JSON error body
      const errorText = await response.text();
      let parsedError: any = {};

      try {
        parsedError = JSON.parse(errorText);
      } catch (e) {
        console.warn("Failed to parse error body as JSON:", errorText);
      }

      const rawMessage = parsedError?.detail?.error || "Failed to add team member";
      const err = new Error(rawMessage);
      (err as any).raw = parsedError;
      throw err;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update team member:", error);
    throw error;
  }
};

export const teamMemberDeleteCall = async (
  accessToken: string,
  teamId: string,
  formValues: Member, // Assuming formValues is an object
) => {
  try {
    const data = await apiClient.post(`/team/member_delete`, {
      accessToken,
      body: {
        team_id: teamId,
        ...(formValues.user_email !== undefined && {
          user_email: formValues.user_email,
        }),
        ...(formValues.user_id !== undefined && {
          user_id: formValues.user_id,
        }),
      },
    });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const getDefaultTeamSettings = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/get/default_team_settings`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to fetch default team settings:", error);
    throw error;
  }
};

export const updateDefaultTeamSettings = async (accessToken: string, settings: Record<string, any>) => {
  try {
    const data = await apiClient.patch(`/update/default_team_settings`, { accessToken, body: settings });
    return data;
  } catch (error) {
    console.error("Failed to update default team settings:", error);
    throw error;
  }
};

export const getTeamPermissionsCall = async (accessToken: string, teamId: string) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/team/permissions_list?team_id=${teamId}`
      : `/team/permissions_list?team_id=${teamId}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      console.error("Available permissions fetch failed:", errorMessage);
      return { all_available_permissions: [], team_member_permissions: [] };
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get team permissions:", error);
    throw error;
  }
};

export const teamPermissionsUpdateCall = async (accessToken: string, teamId: string, permissions: string[]) => {
  try {
    const data = await apiClient.post(`/team/permissions_update`, {
      accessToken,
      body: {
        team_id: teamId,
        team_member_permissions: permissions,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to update team permissions:", error);
    throw error;
  }
};
