// 登录与单点登录配置接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, handleError, apiClient, globalLitellmHeaderName, getProxyBaseUrl } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";
import { storeLoginToken } from "@/utils/cookieUtils";

export const getOnboardingCredentials = async (inviteUUID: string) => {
  /**
   * Get all models on proxy
   */
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/onboarding/get_token` : `/onboarding/get_token`;
    url += `?invite_link=${inviteUUID}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
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

export const claimOnboardingToken = async (
  accessToken: string,
  inviteUUID: string,
  userID: string,
  password: string,
) => {
  try {
    const data = await apiClient.post(`/onboarding/claim_token`, {
      accessToken,
      body: {
        invitation_link: inviteUUID,
        user_id: userID,
        password: password,
      },
    });
    return data;
    // Handle success - you might want to update some state or UI based on the created key
  } catch (error) {
    console.error("Failed to delete key:", error);
    throw error;
  }
};

export const getSSOSettings = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/get/sso_settings`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to fetch SSO configuration:", error);
    throw error;
  }
};

export const updateSSOSettings = async (accessToken: string, settings: Record<string, any>) => {
  try {
    // Construct base URL
    let url = proxyBaseUrl ? `${proxyBaseUrl}/update/sso_settings` : `/update/sso_settings`;

    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(settings),
    });

    if (!response.ok) {
      const errorData = await response.json();
      const detailMessage =
        typeof errorData?.detail === "object"
          ? errorData.detail?.error || errorData.detail?.message
          : errorData?.detail;
      const errorMessage =
        typeof detailMessage === "string" && detailMessage.length > 0 ? detailMessage : deriveErrorMessage(errorData);

      handleError(errorMessage);

      const enhancedError = new Error(errorMessage);
      if (errorData?.detail !== undefined) {
        (enhancedError as any).detail = errorData.detail;
      }
      (enhancedError as any).rawError = errorData;

      throw enhancedError;
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update SSO configuration:", error);
    throw error;
  }
};

export interface LoginRequest {
  username: string;
  password: string;
  useV3?: boolean;
}

interface LoginResponse {
  redirect_url: string;
  token?: string;
  code?: string;
  expires_in?: number;
}

export const loginCall = async (username: string, password: string, useV3?: boolean): Promise<LoginResponse> => {
  const proxyBaseUrl = getProxyBaseUrl();
  const loginPath = useV3 ? "/v3/login" : "/v2/login";
  const loginUrl = proxyBaseUrl ? `${proxyBaseUrl}${loginPath}` : loginPath;

  const body = JSON.stringify({
    username,
    password,
  });

  const response = await fetch(loginUrl, {
    method: "POST",
    body,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
  });

  if (!response.ok) {
    const errorData = await response.json();
    const errorMessage = deriveErrorMessage(errorData);
    throw new Error(errorMessage);
  }

  const data: LoginResponse = await response.json();

  // v3 returns an opaque code — exchange it for the real JWT
  if (useV3 && data.code) {
    const exchangeUrl = proxyBaseUrl ? `${proxyBaseUrl}/v3/login/exchange` : "/v3/login/exchange";

    const exchangeResponse = await fetch(exchangeUrl, {
      method: "POST",
      body: JSON.stringify({ code: data.code }),
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    });

    if (!exchangeResponse.ok) {
      const errorData = await exchangeResponse.json();
      throw new Error(deriveErrorMessage(errorData));
    }

    const exchangeData: LoginResponse = await exchangeResponse.json();
    if (exchangeData.token) {
      storeLoginToken(exchangeData.token);
    }
    return exchangeData;
  }

  // Backwards compatibility: v2 or old v3 returns token directly
  if (data.token) {
    storeLoginToken(data.token);
  }

  return data;
};

/**
 * Exchange a single-use login code for a JWT token.
 * Used by the SSO callback when the worker redirects back with ?code=.
 */
export const exchangeLoginCode = async (code: string, workerBaseUrl?: string | null): Promise<string> => {
  const base = workerBaseUrl || getProxyBaseUrl();
  const response = await fetch(`${base}/v3/login/exchange`, {
    method: "POST",
    body: JSON.stringify({ code }),
    headers: { "Content-Type": "application/json" },
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(deriveErrorMessage(errorData));
  }

  const data = await response.json();
  if (data.token) {
    document.cookie = `token=${data.token}; path=/; SameSite=Lax`;
  }
  return data.token;
};
