// 用量统计接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export const adminSpendLogsCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/global/spend/logs`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const adminTopKeysCall = async (accessToken: string) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/global/spend/keys?limit=5` : `/global/spend/keys?limit=5`;

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
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const adminTopEndUsersCall = async (
  accessToken: string,
  keyToken: string | null,
  startTime: string | undefined,
  endTime: string | undefined,
) => {
  try {
    const body = keyToken
      ? { api_key: keyToken, startTime: startTime, endTime: endTime }
      : { startTime: startTime, endTime: endTime };

    const data = await apiClient.post(`/global/spend/end_users`, { accessToken, body });
    return data;
  } catch (error) {
    console.error("Failed to create key:", error);
    throw error;
  }
};

export const adminspendByProvider = async (
  accessToken: string,
  startTime: string | undefined,
  endTime: string | undefined,
) => {
  try {
    const data = await apiClient.get(`/global/spend/provider`, {
      accessToken,
      query: {
        ...(startTime && endTime ? { start_date: startTime, end_date: endTime } : {}),
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to fetch spend data:", error);
    throw error;
  }
};

export const adminGlobalActivity = async (
  accessToken: string,
  startTime: string | undefined,
  endTime: string | undefined,
) => {
  try {
    const data = await apiClient.get(`/global/activity`, {
      accessToken,
      query: startTime && endTime ? { start_date: startTime, end_date: endTime } : undefined,
    });
    return data;
  } catch (error) {
    console.error("Failed to fetch spend data:", error);
    throw error;
  }
};

export const adminGlobalActivityPerModel = async (
  accessToken: string,
  startTime: string | undefined,
  endTime: string | undefined,
) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/global/activity/model` : `/global/activity/model`;

    if (startTime && endTime) {
      url += `?start_date=${startTime}&end_date=${endTime}`;
    }

    const requestOptions = {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    };

    const response = await fetch(url, requestOptions);

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch spend data:", error);
    throw error;
  }
};

export const adminTopModelsCall = async (accessToken: string) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/global/spend/models?limit=5` : `/global/spend/models?limit=5`;

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
    console.error("Failed to create key:", error);
    throw error;
  }
};

export interface UsageAiToolCallEvent {
  tool_name: string;
  tool_label: string;
  arguments: Record<string, string>;
  status: "running" | "complete" | "error";
  error?: string;
}

export const usageAiChatStream = async (
  accessToken: string,
  messages: { role: string; content: string }[],
  model: string,
  onChunk: (content: string) => void,
  onDone: () => void,
  onError?: (error: string) => void,
  onStatus?: (message: string) => void,
  onToolCall?: (event: UsageAiToolCallEvent) => void,
  signal?: AbortSignal,
) => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/usage/ai/chat` : `/usage/ai/chat`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ messages, model }),
    signal,
  });

  if (!response.ok) {
    const errorData = await response.json();
    const errorMessage = deriveErrorMessage(errorData);
    handleError(errorMessage);
    throw new Error(errorMessage);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  // eslint-disable-next-line no-constant-condition -- stream read loop
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(line.slice(6));
        if (event.type === "chunk") {
          onChunk(event.content);
        } else if (event.type === "status") {
          onStatus?.(event.message);
        } else if (event.type === "tool_call") {
          onToolCall?.(event as UsageAiToolCallEvent);
        } else if (event.type === "done") {
          onDone();
        } else if (event.type === "error") {
          onError?.(event.message);
        }
      } catch {
        // skip malformed events
      }
    }
  }
};

export const userAgentSummaryCall = async (
  accessToken: string,
  startTime: Date,
  endTime: Date,
  tagFilters?: string[],
) => {
  /**
   * Get user agent summary statistics
   */
  try {
    const formatDate = (date: Date) => {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    };

    return await apiClient.get(`/tag/summary`, {
      accessToken,
      query: {
        start_date: formatDate(startTime),
        end_date: formatDate(endTime),
        tag_filters: tagFilters && tagFilters.length > 0 ? tagFilters : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch user agent summary:", error);
    throw error;
  }
};

export const perUserAnalyticsCall = async (
  accessToken: string,
  page: number = 1,
  pageSize: number = 50,
  tagFilters?: string[],
) => {
  /**
   * Get per-user analytics data for the last 30 days
   */
  try {
    return await apiClient.get(`/tag/user-agent/per-user-analytics`, {
      accessToken,
      query: {
        page: page.toString(),
        page_size: pageSize.toString(),
        tag_filters: tagFilters && tagFilters.length > 0 ? tagFilters : undefined,
      },
    });
  } catch (error) {
    console.error("Failed to fetch per-user analytics:", error);
    throw error;
  }
};
