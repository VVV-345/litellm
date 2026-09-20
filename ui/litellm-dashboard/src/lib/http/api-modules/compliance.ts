// 合规检查接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName } from "./clientState";

// Compliance check types and functions

export interface ComplianceCheckResult {
  check_name: string;
  article: string;
  passed: boolean;
  detail: string;
}

export interface ComplianceResponse {
  compliant: boolean;
  regulation: string;
  checks: ComplianceCheckResult[];
}

export interface ComplianceCheckRequest {
  request_id: string;
  user_id?: string;
  model?: string;
  timestamp?: string;
  guardrail_information?: Record<string, any>[];
}

export const checkEuAiActCompliance = async (
  accessToken: string,
  payload: ComplianceCheckRequest,
): Promise<ComplianceResponse> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/compliance/eu-ai-act` : `/compliance/eu-ai-act`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};

export const checkGdprCompliance = async (
  accessToken: string,
  payload: ComplianceCheckRequest,
): Promise<ComplianceResponse> => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/compliance/gdpr` : `/compliance/gdpr`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorData = await response.text();
    throw new Error(errorData);
  }
  return response.json();
};
