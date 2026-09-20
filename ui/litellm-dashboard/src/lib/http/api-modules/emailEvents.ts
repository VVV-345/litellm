// 邮件事件配置接口；共用客户端状态，保持既有请求契约。
import { EmailEventSettingsResponse, EmailEventSettingsUpdateRequest } from "@/components/email_events/types";
import { proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";

export const getEmailEventSettings = async (accessToken: string): Promise<EmailEventSettingsResponse> => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/email/event_settings` : `/email/event_settings`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to get email event settings");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get email event settings:", error);
    throw error;
  }
};

export const updateEmailEventSettings = async (accessToken: string, settings: EmailEventSettingsUpdateRequest) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/email/event_settings` : `/email/event_settings`;

    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(settings),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to update email event settings");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update email event settings:", error);
    throw error;
  }
};

export const resetEmailEventSettings = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/email/event_settings/reset` : `/email/event_settings/reset`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to reset email event settings");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to reset email event settings:", error);
    throw error;
  }
};
