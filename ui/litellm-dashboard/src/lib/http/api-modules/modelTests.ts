// 模型连通性与路由测试接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage, extractProxyErrorMessage } from "@/lib/http/client";
import type { ComplexityRouterConfigPayload } from "@/components/add_model/build_complexity_router_config";
import type { RoutingDecision } from "@/components/view_logs/LogDetailsDrawer/RoutingDecisionCard";

export const getAutoRouterClassifierDefaultPromptCall = async (
  accessToken: string,
  contextWindowSize: number,
  tierLabels?: Record<string, string>,
  classificationRubric?: string,
): Promise<string> => {
  /**
   * Get the built-in system prompt an auto-router's LLM classifier uses when none is configured,
   * so the prompt editor prefills what the proxy actually sends rather than a frontend copy.
   *
   * tierLabels names the rubric's tier bullets, so a router that renamed its tiers prefills the
   * rubric it sends rather than one using the canonical names. rubric selects which calibration
   * examples it carries, for the same reason.
   */
  try {
    const response = await apiClient.get<{ system_prompt: string }>(`/auto_router/classifier/default_prompt`, {
      accessToken,
      query: {
        context_window_size: contextWindowSize,
        ...(tierLabels && Object.keys(tierLabels).length > 0 ? { tier_labels: JSON.stringify(tierLabels) } : {}),
        ...(classificationRubric ? { classification_rubric: classificationRubric } : {}),
      },
    });
    return response.system_prompt;
  } catch (error) {
    console.error("Failed to get the default classifier prompt:", error);
    throw error;
  }
};

export const getAutoRouterCustomTierPromptCall = async (
  accessToken: string,
  contextWindowSize: number,
  tierDefinitions: { name: string; description?: string }[],
  classificationPrompt?: string,
): Promise<string> => {
  /**
   * Assembled by the proxy, because a built-in name with no description inherits criteria that live
   * only in the backend. POSTed so the operator's prompt does not reach access logs through a URL.
   */
  const response = await apiClient.post<{ system_prompt: string }>(`/auto_router/classifier/default_prompt`, {
    accessToken,
    body: {
      context_window_size: contextWindowSize,
      tier_definitions: tierDefinitions,
      ...(classificationPrompt?.trim() ? { classification_prompt: classificationPrompt } : {}),
    },
  });
  return response.system_prompt;
};

export interface ComplexityScorerDefaults {
  tier_boundaries: Record<string, number>;
  token_thresholds: Record<string, number>;
  dimension_weights: Record<string, number>;
}

export const getComplexityScorerDefaults = async (): Promise<ComplexityScorerDefaults> => {
  /**
   * Fetch the complexity router's shipped heuristic scorer defaults from the proxy's public endpoint.
   * The Advanced scoring controls prefill from these rather than from a copy in the dashboard, so a
   * recalibration of the defaults cannot leave the form reporting numbers the router no longer uses.
   */
  return await apiClient.get(`/public/complexity_router/scorer_defaults`);
};

export const transformRequestCall = async (accessToken: string, request: object) => {
  /**
   * Transform request
   */

  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/utils/transform_request` : `/utils/transform_request`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
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

export const testConnectionRequest = async (
  accessToken: string,
  litellm_params: Record<string, any>,
  model_info: Record<string, any>,
  mode: string,
) => {
  try {
    // Construct the URL based on environment
    const url = proxyBaseUrl ? `${proxyBaseUrl}/health/test_connection` : `/health/test_connection`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        litellm_params: litellm_params,
        model_info: model_info,
        mode: mode,
      }),
    });

    // Check for non-JSON responses first
    const contentType = response.headers.get("content-type");
    if (!contentType || !contentType.includes("application/json")) {
      const text = await response.text();
      console.error("Received non-JSON response:", text);
      throw new Error(
        `Received non-JSON response (${response.status}: ${response.statusText}). Check network tab for details.`,
      );
    }

    const data = await response.json();

    if (!response.ok || data.status === "error") {
      // Return the error response instead of throwing an error
      // This allows the caller to handle the error format properly
      if (data.status === "error") {
        return data; // Return the full error response
      } else {
        return {
          status: "error",
          message: data.error?.message || `Connection test failed: ${response.status} ${response.statusText}`,
        };
      }
    }

    return data;
  } catch (error) {
    console.error("Model connection test error:", error);
    // For network errors or other exceptions, still throw
    throw error;
  }
};

export type ModelGroupConnectionResult = { status: "success" } | { status: "error"; error: string };

/**
 * Test an existing model group by routing a minimal request through the proxy
 * exactly as production would (by public model_group name). Unlike
 * /health/test_connection, this needs no litellm_params resolution: the router
 * resolves the group, credentials, and provider. Used by the auto-router Test
 * Connection to probe each tier's model group and the embedding model.
 */
/**
 * Build the minimal request that probes a model group by public name. No
 * max_tokens: reasoning models (o1/o3/...) reject a tiny cap with "max_tokens
 * reached" because reasoning tokens count against it, which would show a false
 * failure for a reachable tier.
 */
export const buildModelGroupTestRequest = (
  modelGroup: string,
  mode: "chat" | "embedding",
): { path: string; body: Record<string, unknown> } =>
  mode === "embedding"
    ? { path: "/v1/embeddings", body: { model: modelGroup, input: "test from litellm" } }
    : {
        path: "/v1/chat/completions",
        body: { model: modelGroup, messages: [{ role: "user", content: "test from litellm" }] },
      };

export const testModelGroupConnection = async (
  accessToken: string,
  modelGroup: string,
  mode: "chat" | "embedding",
): Promise<ModelGroupConnectionResult> => {
  const { path, body } = buildModelGroupTestRequest(modelGroup, mode);
  try {
    await apiClient.post(path, { accessToken, body });
    return { status: "success" };
  } catch (error) {
    return { status: "error", error: error instanceof Error ? error.message : String(error) };
  }
};

export interface AutoRouterRoutingTestRequest {
  prompt: string;
  complexity_router_config: ComplexityRouterConfigPayload;
  default_model?: string;
  router_name?: string;
  team_id?: string;
}

export interface AutoRouterRoutingTestResult {
  routed_model: string;
  routed_model_configured: boolean;
  routing_decision: RoutingDecision;
}

export type AutoRouterRoutingTestResponse =
  | { status: "success"; result: AutoRouterRoutingTestResult }
  | { status: "error"; error: string };

export const testAutoRouterRouting = async (
  accessToken: string,
  request: AutoRouterRoutingTestRequest,
): Promise<AutoRouterRoutingTestResponse> => {
  try {
    const result = await apiClient.post<AutoRouterRoutingTestResult>("/auto_router/test_routing", {
      accessToken,
      body: request,
    });
    return { status: "success", result };
  } catch (error) {
    return { status: "error", error: extractProxyErrorMessage(error) };
  }
};

export interface ComplexityRouterConfigValidation {
  valid: boolean;
  error?: string | null;
}

// Dry-runs the same write gate /model/new and /model/update apply, so a save that would come back
// as a raw 400 shows the backend's own message inline first. Transport failures fail open: the
// write gate stays authoritative.
export const validateAutoRouterConfig = async (
  accessToken: string,
  complexityRouterConfig: Record<string, unknown>,
  teamId?: string,
): Promise<ComplexityRouterConfigValidation> => {
  try {
    return await apiClient.post<ComplexityRouterConfigValidation>("/auto_router/validate_complexity_router_config", {
      accessToken,
      body: { complexity_router_config: complexityRouterConfig, ...(teamId && { team_id: teamId }) },
    });
  } catch (error) {
    console.warn("Could not dry-run the complexity router config; the save will be validated server side", error);
    return { valid: true };
  }
};
