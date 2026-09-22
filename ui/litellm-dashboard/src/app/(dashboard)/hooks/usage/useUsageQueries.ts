import { queryOptions } from "@tanstack/react-query";

import { gatewayDailyActivityCall, tagListCall, userDailyActivityAggregatedCall } from "@/components/networking";
import type { TagListResponse } from "@/components/tag_management/types";
import type { DailyData } from "@/components/UsagePage/types";

interface UsageActivity {
  results: DailyData[];
  metadata: Record<string, number>;
}

const usageKeys = {
  all: ["usage"] as const,
  dailyActivity: (accessToken: string | null, startTime: Date | null, endTime: Date | null, userId: string | null) =>
    [
      "usage",
      "daily-activity-aggregated",
      accessToken,
      formatDateKey(startTime),
      formatDateKey(endTime),
      String(new Date().getTimezoneOffset()),
      userId,
    ] as const,
  gatewayActivity: (accessToken: string | null, startTime: Date | null, endTime: Date | null) =>
    ["usage", "gateway-daily-activity", accessToken, formatDateKey(startTime), formatDateKey(endTime)] as const,
  tags: (accessToken: string | null, startTime: Date | null, endTime: Date | null) =>
    ["usage", "tags", accessToken, formatDateKey(startTime), formatDateKey(endTime)] as const,
};

const formatDateKey = (value: Date | null): string => {
  if (!value) return "";
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
};

export const usageDailyActivityQueryOptions = (
  accessToken: string | null,
  startTime: Date | null,
  endTime: Date | null,
  userId: string | null,
) =>
  queryOptions({
    queryKey: usageKeys.dailyActivity(accessToken, startTime, endTime, userId),
    queryFn: (): Promise<UsageActivity> => {
      if (!accessToken || !startTime || !endTime) throw new Error("Usage range is not ready");
      return userDailyActivityAggregatedCall(accessToken, startTime, endTime, userId);
    },
    enabled: Boolean(accessToken && startTime && endTime),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

export const usageGatewayActivityQueryOptions = (
  accessToken: string | null,
  startTime: Date | null,
  endTime: Date | null,
  enabled: boolean,
) =>
  queryOptions({
    queryKey: usageKeys.gatewayActivity(accessToken, startTime, endTime),
    queryFn: () => {
      if (!accessToken || !startTime || !endTime) throw new Error("Usage range is not ready");
      return gatewayDailyActivityCall(accessToken, startTime, endTime);
    },
    enabled: enabled && Boolean(accessToken && startTime && endTime),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

export const usageTagsQueryOptions = (accessToken: string | null, startTime: Date | null, endTime: Date | null) =>
  queryOptions<TagListResponse>({
    queryKey: usageKeys.tags(accessToken, startTime, endTime),
    queryFn: () => {
      if (!accessToken || !startTime || !endTime) throw new Error("Usage range is not ready");
      return tagListCall(accessToken, startTime, endTime);
    },
    enabled: Boolean(accessToken && startTime && endTime),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
