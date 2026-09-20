// 标签管理接口；共用客户端状态，保持既有请求契约。
import { TagNewRequest, TagUpdateRequest, TagInfoResponse, TagListResponse } from "@/components/tag_management/types";
import { proxyBaseUrl, globalLitellmHeaderName, handleError, apiClient } from "./clientState";

export const tagCreateCall = async (accessToken: string, formValues: TagNewRequest): Promise<void> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/tag/new` : `/tag/new`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(formValues),
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return;
    }

    return await response.json();
  } catch (error) {
    console.error("Error creating tag:", error);
    throw error;
  }
};

export const tagUpdateCall = async (accessToken: string, formValues: TagUpdateRequest): Promise<void> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/tag/update` : `/tag/update`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(formValues),
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return;
    }

    return await response.json();
  } catch (error) {
    console.error("Error updating tag:", error);
    throw error;
  }
};

export const tagInfoCall = async (accessToken: string, tagNames: string[]): Promise<TagInfoResponse> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/tag/info` : `/tag/info`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ names: tagNames }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return {};
    }

    const data = await response.json();
    return data as TagInfoResponse;
  } catch (error) {
    console.error("Error getting tag info:", error);
    throw error;
  }
};

const formatYmd = (value: Date): string => {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

export const tagListCall = async (
  accessToken: string,
  startTime?: Date | null,
  endTime?: Date | null,
): Promise<TagListResponse> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/tag/list` : `/tag/list`;

    if (startTime && endTime) {
      const params = new URLSearchParams({
        start_date: formatYmd(startTime),
        end_date: formatYmd(endTime),
      });
      url = `${url}?${params.toString()}`;
    }

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return {};
    }

    const data = await response.json();
    return data as TagListResponse;
  } catch (error) {
    console.error("Error listing tags:", error);
    throw error;
  }
};

export const tagDeleteCall = async (accessToken: string, tagName: string): Promise<void> => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/tag/delete` : `/tag/delete`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ name: tagName }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      await handleError(errorData);
      return;
    }

    return await response.json();
  } catch (error) {
    console.error("Error deleting tag:", error);
    throw error;
  }
};

// New endpoint functions for DAU, WAU, MAU
export const tagDauCall = async (accessToken: string, endDate: Date, tagFilter?: string, tagFilters?: string[]) => {
  /**
   * Get Daily Active Users (DAU) for last 7 days ending on endDate
   */
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    const hasTagFilters = tagFilters && tagFilters.length > 0;
    return await apiClient.get(`/tag/dau`, {
      accessToken,
      query: {
        end_date: formatDate(endDate),
        tag_filters: hasTagFilters ? tagFilters : undefined,
        tag_filter: !hasTagFilters && tagFilter ? tagFilter : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch DAU:", error);
    throw error;
  }
};

export const tagWauCall = async (accessToken: string, endDate: Date, tagFilter?: string, tagFilters?: string[]) => {
  /**
   * Get Weekly Active Users (WAU) for last 7 weeks ending on endDate
   */
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    const hasTagFilters = tagFilters && tagFilters.length > 0;
    return await apiClient.get(`/tag/wau`, {
      accessToken,
      query: {
        end_date: formatDate(endDate),
        tag_filters: hasTagFilters ? tagFilters : undefined,
        tag_filter: !hasTagFilters && tagFilter ? tagFilter : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch WAU:", error);
    throw error;
  }
};

export const tagMauCall = async (accessToken: string, endDate: Date, tagFilter?: string, tagFilters?: string[]) => {
  /**
   * Get Monthly Active Users (MAU) for last 7 months ending on endDate
   */
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    const hasTagFilters = tagFilters && tagFilters.length > 0;
    return await apiClient.get(`/tag/mau`, {
      accessToken,
      query: {
        end_date: formatDate(endDate),
        tag_filters: hasTagFilters ? tagFilters : undefined,
        tag_filter: !hasTagFilters && tagFilter ? tagFilter : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch MAU:", error);
    throw error;
  }
};

export const tagDistinctCall = async (accessToken: string) => {
  /**
   * Get all distinct user agent tags (up to 250)
   */
  try {
    const data = await apiClient.get(`/tag/distinct`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to fetch distinct tags:", error);
    throw error;
  }
};
