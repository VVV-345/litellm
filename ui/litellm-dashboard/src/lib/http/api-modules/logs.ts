// 调用与审计日志接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export const teamSpendLogsCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/global/spend/teams`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const tagsSpendLogsCall = async (
  accessToken: string,
  startTime: string | undefined,
  endTime: string | undefined,
  tags: string[] | undefined,
) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/global/spend/tags` : `/global/spend/tags`;

    if (startTime && endTime) {
      url = `${url}?start_date=${startTime}&end_date=${endTime}`;
    }

    // if tags, convert the list to a comma separated string
    if (tags) {
      url += `&tags=${tags.join(",")}`;
    }

    const response = await fetch(`${url}`, {
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
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const allTagNamesCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/global/spend/all_tag_names`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const allEndUsersCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/customer/list`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to fetch end users:", error);
    throw error;
  }
};

export const userFilterUICall = async (accessToken: string, params: URLSearchParams) => {
  try {
    return await apiClient.get(`/user/filter/ui`, {
      accessToken,
      query: {
        user_email: params.get("user_email") || undefined,
        user_id: params.get("user_id") || undefined,
        team_id: params.get("team_id") || undefined,
      },
    });
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

/**
 * Optional query params for /spend/logs/ui - matches backend spend_management_endpoints.py
 */
interface UiSpendLogsParams {
  account_id?: string;
  api_key?: string;
  team_id?: string;
  request_id?: string;
  session_id?: string;
  user_id?: string;
  end_user?: string;
  status_filter?: string;
  cache_hit_filter?: string;
  /** Filter by model name (e.g. "gpt-4") */
  model?: string;
  /** Filter by model ID (litellm model deployment id) */
  model_id?: string;
  key_alias?: string;
  error_code?: string;
  error_message?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  min_spend?: number;
  max_spend?: number;
  exclude_internal_health_checks?: boolean;
}

interface UiSpendLogsCallOptions {
  accessToken: string;
  start_date: string;
  end_date: string;
  page?: number;
  page_size?: number;
  params?: UiSpendLogsParams;
}

export const uiSpendLogsCall = async ({
  accessToken,
  start_date,
  end_date,
  page = 1,
  page_size = 50,
  params = {},
}: UiSpendLogsCallOptions) => {
  try {
    // Construct base URL
    let url = proxyBaseUrl ? `${proxyBaseUrl}/spend/logs/ui` : `/spend/logs/ui`;

    const queryParams = new URLSearchParams();
    queryParams.append("start_date", start_date);
    queryParams.append("end_date", end_date);
    queryParams.append("page", page.toString());
    queryParams.append("page_size", page_size.toString());

    // Add optional params only when explicitly provided
    for (const [key, value] of Object.entries(params)) {
      if (value == null) continue;
      if (key === "min_spend" || key === "max_spend") {
        queryParams.append(key, value.toString());
      } else if (typeof value === "boolean") {
        if (value) queryParams.append(key, "true");
      } else if (typeof value === "string" && value !== "") {
        queryParams.append(key, String(value));
      }
    }

    const queryString = queryParams.toString();
    if (queryString) {
      url += `?${queryString}`;
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
  } catch (error) {
    console.error("Failed to fetch spend logs:", error);
    throw error;
  }
};

export const uiSpendLogDetailsCall = async (accessToken: string, logId: string, start_date: string) => {
  try {
    // Construct base URL
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/spend/logs/ui/${logId}?start_date=${encodeURIComponent(start_date)}`
      : `/spend/logs/ui/${logId}?start_date=${encodeURIComponent(start_date)}`;

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
    console.error("Failed to fetch log details:", error);
    throw error;
  }
};

/**
 * Get a page of spend logs for a particular session.
 *
 * The backend paginates this endpoint (page / page_size, returning
 * { data, total, page, page_size, total_pages }). Callers that need the whole
 * session should page through total_pages and accumulate the results.
 */
export const sessionSpendLogsCall = async (
  accessToken: string,
  session_id: string,
  page: number = 1,
  page_size: number = 100,
) => {
  try {
    const params = new URLSearchParams({
      session_id,
      page: String(page),
      page_size: String(page_size),
    });
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/spend/logs/session/ui?${params.toString()}`
      : `/spend/logs/session/ui?${params.toString()}`;

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
    console.error("Failed to fetch session logs:", error);
    throw error;
  }
};

interface UiAuditLogsParams {
  action?: string;
  table_name?: string;
  object_id?: string;
  changed_by?: string;
  changed_by_api_key?: string;
  object_team_id?: string;
  object_key_hash?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
}

interface UiAuditLogsCallOptions {
  accessToken: string;
  page?: number;
  page_size?: number;
  params?: UiAuditLogsParams;
}

export const uiAuditLogsCall = async ({
  accessToken,
  page = 1,
  page_size = 50,
  params = {},
}: UiAuditLogsCallOptions) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/audit` : `/audit`;

    const queryParams = new URLSearchParams();
    queryParams.append("page", page.toString());
    queryParams.append("page_size", page_size.toString());

    for (const [key, value] of Object.entries(params)) {
      if (value != null && value !== "") {
        queryParams.append(key, String(value));
      }
    }

    url += `?${queryParams.toString()}`;

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

    return await response.json();
  } catch (error) {
    console.error("Failed to fetch audit logs:", error);
    throw error;
  }
};
