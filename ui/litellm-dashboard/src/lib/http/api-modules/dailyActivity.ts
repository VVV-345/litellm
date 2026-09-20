// 每日活动查询接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, handleError, apiClient } from "./clientState";
import { formatDate } from "./dates";
import { deriveErrorMessage } from "@/lib/http/client";

type DailyActivityQueryValue = string | number | string[] | null | undefined;

const DEFAULT_DAILY_ACTIVITY_PAGE_SIZE = "1000";

const appendDailyActivityQueryParam = (params: URLSearchParams, key: string, value: DailyActivityQueryValue) => {
  if (value === null || value === undefined) {
    return;
  }

  if (Array.isArray(value)) {
    if (value.length > 0) {
      params.append(key, value.join(","));
    }
    return;
  }

  params.append(key, `${value}`);
};

const buildDailyActivityUrl = (
  endpoint: string,
  startTime: Date,
  endTime: Date,
  page: number,
  extraQueryParams?: Record<string, DailyActivityQueryValue>,
) => {
  const resolvedEndpoint = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  const baseUrl = proxyBaseUrl ? `${proxyBaseUrl}${resolvedEndpoint}` : resolvedEndpoint;

  const params = new URLSearchParams();
  params.append("start_date", formatDate(startTime));
  params.append("end_date", formatDate(endTime));
  params.append("page_size", DEFAULT_DAILY_ACTIVITY_PAGE_SIZE);
  params.append("page", page.toString());
  // Send timezone offset so backend can adjust date range for UTC storage
  params.append("timezone", new Date().getTimezoneOffset().toString());

  if (extraQueryParams) {
    Object.entries(extraQueryParams).forEach(([key, value]) => {
      appendDailyActivityQueryParam(params, key, value);
    });
  }

  const queryString = params.toString();
  return queryString ? `${baseUrl}?${queryString}` : baseUrl;
};

type DailyActivityCallOptions = {
  accessToken: string;
  endpoint: string;
  startTime: Date;
  endTime: Date;
  page?: number;
  extraQueryParams?: Record<string, DailyActivityQueryValue>;
};

const fetchDailyActivity = async ({
  accessToken,
  endpoint,
  startTime,
  endTime,
  page = 1,
  extraQueryParams,
}: DailyActivityCallOptions) => {
  try {
    const url = buildDailyActivityUrl(endpoint, startTime, endTime, page, extraQueryParams);

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
    console.error(`Failed to fetch daily activity (${endpoint}):`, error);
    throw error;
  }
};

export const userDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  userId: string | null = null,
  includeCurrentUtcDay: boolean = false,
  apiKey: string | null = null,
) => {
  /**
   * Get daily user activity on proxy
   */
  return fetchDailyActivity({
    accessToken,
    endpoint: "/user/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      user_id: userId,
      include_current_utc_day: includeCurrentUtcDay ? "true" : undefined,
      api_key: apiKey,
    },
  });
};

export const tagDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  tags: string[] | null = null,
) => {
  /**
   * Get daily user activity on proxy
   */
  return fetchDailyActivity({
    accessToken,
    endpoint: "/tag/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      tags,
    },
  });
};

export const teamDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  teamIds: string[] | null = null,
) => {
  /**
   * Get daily user activity on proxy
   */
  return fetchDailyActivity({
    accessToken,
    endpoint: "/team/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      team_ids: teamIds,
      exclude_team_ids: "litellm-dashboard",
    },
  });
};

export const teamDailyActivityAggregatedCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  teamIds: string[] | null = null,
) => {
  /**
   * Get aggregated daily team activity with per-team breakdown (no pagination)
   */
  try {
    return await apiClient.get(`/team/daily/activity/aggregated`, {
      accessToken,
      query: {
        start_date: formatDate(startTime),
        end_date: formatDate(endTime),
        timezone: new Date().getTimezoneOffset().toString(),
        team_ids: teamIds && teamIds.length > 0 ? teamIds.join(",") : undefined,
        exclude_team_ids: "litellm-dashboard",
      },
    });
  } catch (error) {
    console.error("Failed to fetch aggregated team daily activity:", error);
    throw error;
  }
};

export const organizationDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  organizationIds: string[] | null = null,
) => {
  return fetchDailyActivity({
    accessToken,
    endpoint: "/organization/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      organization_ids: organizationIds,
    },
  });
};

export const customerDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  customerIds: string[] | null = null,
) => {
  return fetchDailyActivity({
    accessToken,
    endpoint: "/customer/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      end_user_ids: customerIds,
    },
  });
};

export const agentDailyActivityCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  page: number = 1,
  agentIds: string[] | null = null,
) => {
  return fetchDailyActivity({
    accessToken,
    endpoint: "/agent/daily/activity",
    startTime,
    endTime,
    page,
    extraQueryParams: {
      agent_ids: agentIds,
    },
  });
};

export const userDailyActivityAggregatedCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  ...options: [userId?: string | null, includeCurrentUtcDay?: boolean, apiKey?: string | null]
) => {
  /**
   * Get aggregated daily user activity (no pagination)
   */
  const [userId = null, includeCurrentUtcDay = false, apiKey = null] = options;
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };
    return await apiClient.get(`/user/daily/activity/aggregated`, {
      accessToken,
      query: {
        start_date: formatDate(startTime),
        end_date: formatDate(endTime),
        timezone: new Date().getTimezoneOffset().toString(),
        // Passed raw, matching the paginated caller: both serializers drop null and undefined,
        // and both keep "". An empty filter must not vanish, or a request scoped to one user or
        // key would silently widen into an unscoped, proxy-wide read.
        user_id: userId,
        include_current_utc_day: includeCurrentUtcDay ? "true" : undefined,
        api_key: apiKey,
      },
    });
  } catch (error) {
    console.error("Failed to fetch aggregated user daily activity:", error);
    throw error;
  }
};

export const gatewayDailyActivityCall = async (accessToken: string, startTime: Date, endTime: Date) => {
  /**
   * Get gateway request counts (SGR) recorded by the proxy middleware.
   * Deployment-wide and admin-only; carries no per-key or per-user dimension.
   */
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };
    return await apiClient.get(`/gateway/daily/activity`, {
      accessToken,
      query: {
        start_date: formatDate(startTime),
        end_date: formatDate(endTime),
      },
    });
  } catch (error) {
    console.error("Failed to fetch gateway daily activity:", error);
    throw error;
  }
};
