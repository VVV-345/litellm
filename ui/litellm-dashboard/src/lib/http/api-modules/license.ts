// 许可证信息接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";

export const getRemainingUsers = async (
  accessToken: string,
): Promise<{
  total_users: number | null;
  total_users_used: number;
  total_users_remaining: number | null;
  total_teams: number | null;
  total_teams_used: number;
  total_teams_remaining: number | null;
} | null> => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/user/available_users` : `/user/available_users`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      // if 404 - return None
      if (response.status === 404) {
        return null;
      }
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Network response was not ok");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch remaining users:", error);
    throw error;
  }
};

export interface LicenseInfo {
  has_license: boolean;
  license_type: string | null;
  expiration_date: string | null;
  allowed_features: string[];
  limits: {
    max_users: number | null;
    max_teams: number | null;
  };
}

export const getLicenseInfo = async (accessToken: string): Promise<LicenseInfo | null> => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/health/license` : `/health/license`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      // if 404 - return null (endpoint not available)
      if (response.status === 404) {
        return null;
      }
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Network response was not ok");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to fetch license info:", error);
    throw error;
  }
};
