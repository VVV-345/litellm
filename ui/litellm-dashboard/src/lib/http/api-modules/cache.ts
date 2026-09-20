// 缓存配置接口；共用客户端状态，保持既有请求契约。
import { apiClient } from "./clientState";
import type {
  CoordinationRedisSettingsResponse,
  CoordinationRedisSettings,
  CoordinationRedisTestResponse,
} from "@/app/(dashboard)/caching/_components/coordination_redis_settings/types";

export const getCacheSettingsCall = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/cache/settings`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get cache settings:", error);
    throw error;
  }
};

export const testCacheConnectionCall = async (accessToken: string, cacheSettings: Record<string, any>) => {
  try {
    const data = await apiClient.post(`/cache/settings/test`, {
      accessToken,
      body: {
        cache_settings: cacheSettings,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to test cache connection:", error);
    throw error;
  }
};

export const updateCacheSettingsCall = async (accessToken: string, cacheSettings: Record<string, any>) => {
  try {
    const data = await apiClient.post(`/cache/settings`, {
      accessToken,
      body: {
        cache_settings: cacheSettings,
      },
    });
    return data;
  } catch (error) {
    console.error("Failed to update cache settings:", error);
    throw error;
  }
};

export const getCoordinationRedisSettingsCall = async (
  accessToken: string,
): Promise<CoordinationRedisSettingsResponse> => {
  try {
    return await apiClient.get<CoordinationRedisSettingsResponse>(`/coordination_redis/settings`, { accessToken });
  } catch (error) {
    console.error("Failed to get coordination redis settings:", error);
    throw error;
  }
};

export const testCoordinationRedisConnectionCall = async (
  accessToken: string,
  settings: CoordinationRedisSettings,
): Promise<CoordinationRedisTestResponse> => {
  try {
    return await apiClient.post<CoordinationRedisTestResponse>(`/coordination_redis/settings/test`, {
      accessToken,
      body: { settings },
    });
  } catch (error) {
    console.error("Failed to test coordination redis connection:", error);
    throw error;
  }
};

export const updateCoordinationRedisSettingsCall = async (
  accessToken: string,
  settings: CoordinationRedisSettings,
): Promise<void> => {
  try {
    await apiClient.post(`/coordination_redis/settings`, {
      accessToken,
      body: { settings },
    });
  } catch (error) {
    console.error("Failed to update coordination redis settings:", error);
    throw error;
  }
};
