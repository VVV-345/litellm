// 策略管理接口；共用客户端状态，保持既有请求契约。
import { apiClient, proxyBaseUrl, globalLitellmHeaderName, handleError } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

// ─────────────────────────────────────────────────────────────────────────────
// Policy CRUD API Calls
// ─────────────────────────────────────────────────────────────────────────────

export const getPoliciesList = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/policies/list`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get policies list:", error);
    throw error;
  }
};

interface GuardrailInputs {
  texts?: string[];
  images?: string[];
  [key: string]: unknown;
}

interface TestPoliciesAndGuardrailsRequest {
  policy_names?: string[] | null;
  guardrail_names?: string[] | null;
  /** Single input (legacy). Use inputs_list for per-input batch processing. */
  inputs?: GuardrailInputs | null;
  /** List of inputs; each processed separately for batch compliance testing. */
  inputs_list?: GuardrailInputs[] | null;
  request_data?: Record<string, unknown>;
  input_type?: "request" | "response";
  /** When set, backend runs chat completion with this model/agent per input and includes agent_response in each result. */
  agent_id?: string | null;
}

interface GuardrailErrorEntry {
  guardrail_name: string;
  message: string;
}

interface TestPoliciesAndGuardrailsResultItem {
  inputs: Record<string, unknown>;
  guardrail_errors: GuardrailErrorEntry[];
  /** Present when request included agent_id; serialized chat completion response. */
  agent_response?: Record<string, unknown>;
}

interface TestPoliciesAndGuardrailsResponse {
  inputs?: Record<string, unknown>;
  guardrail_errors?: GuardrailErrorEntry[];
  /** Present when inputs_list was used; one result per input. */
  results?: TestPoliciesAndGuardrailsResultItem[];
}

export const testPoliciesAndGuardrails = async (
  accessToken: string,
  body: TestPoliciesAndGuardrailsRequest,
  signal?: AbortSignal,
): Promise<TestPoliciesAndGuardrailsResponse> => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/utils/test_policies_and_guardrails`
      : `/utils/test_policies_and_guardrails`;
    const response = await fetch(url, {
      method: "POST",
      signal,
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        policy_names: body.policy_names ?? null,
        guardrail_names: body.guardrail_names ?? null,
        inputs: body.inputs ?? null,
        inputs_list: body.inputs_list ?? null,
        request_data: body.request_data ?? {},
        input_type: body.input_type ?? "request",
        agent_id: body.agent_id ?? null,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      let errorMessage = "Failed to test policies and guardrails";
      try {
        const errorJson = JSON.parse(errorData);
        if (errorJson.detail)
          errorMessage = typeof errorJson.detail === "string" ? errorJson.detail : JSON.stringify(errorJson.detail);
        else if (errorJson.message) errorMessage = errorJson.message;
      } catch {
        errorMessage = errorData || errorMessage;
      }
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to test policies and guardrails:", error);
    throw error;
  }
};

export const getPolicyInfoWithGuardrails = async (accessToken: string, policyName: string) => {
  try {
    const data = await apiClient.get(`/policy/info/${policyName}`, { accessToken });
    return data;
  } catch (error) {
    console.error(`Failed to get policy info for ${policyName}:`, error);
    throw error;
  }
};

export const getPolicyTemplates = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/policy/templates`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get policy templates:", error);
    throw error;
  }
};

export const enrichPolicyTemplate = async (
  accessToken: string,
  templateId: string,
  parameters: Record<string, string>,
  model?: string,
  competitors?: string[],
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/policy/templates/enrich` : `/policy/templates/enrich`;
    const body: any = { template_id: templateId, parameters };
    if (model) body.model = model;
    if (competitors) body.competitors = competitors;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
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
    console.error("Failed to enrich policy template:", error);
    throw error;
  }
};

export const suggestPolicyTemplates = async (
  accessToken: string,
  attackExamples: string[],
  description: string,
  model: string,
) => {
  try {
    return await apiClient.post(`/policy/templates/suggest`, {
      accessToken,
      body: {
        attack_examples: attackExamples.filter((e) => e.trim()),
        description,
        model,
      },
    });
  } catch (error) {
    console.error("Failed to suggest policy templates:", error);
    throw error;
  }
};

export const testPolicyTemplate = async (accessToken: string, guardrailDefinitions: any[], text: string) => {
  try {
    return await apiClient.post(`/policy/templates/test`, {
      accessToken,
      body: {
        guardrail_definitions: guardrailDefinitions,
        text,
      },
    });
  } catch (error) {
    console.error("Failed to test policy template:", error);
    throw error;
  }
};

export const enrichPolicyTemplateStream = async (
  accessToken: string,
  templateId: string,
  parameters: Record<string, string>,
  model: string,
  onCompetitor: (name: string) => void,
  onDone: (result: {
    competitors: string[];
    competitor_variations: Record<string, string[]>;
    guardrailDefinitions: any[];
  }) => void,
  onError?: (error: string) => void,
  options?: { instruction?: string; existingCompetitors?: string[] },
  onStatus?: (message: string) => void,
) => {
  const url = proxyBaseUrl ? `${proxyBaseUrl}/policy/templates/enrich/stream` : `/policy/templates/enrich/stream`;
  const body: any = { template_id: templateId, parameters, model };
  if (options?.instruction) body.instruction = options.instruction;
  if (options?.existingCompetitors) body.competitors = options.existingCompetitors;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      [globalLitellmHeaderName]: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
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
        if (event.type === "competitor") {
          onCompetitor(event.name);
        } else if (event.type === "status") {
          onStatus?.(event.message);
        } else if (event.type === "done") {
          onDone(event);
        } else if (event.type === "error") {
          onError?.(event.message);
        }
      } catch {
        // skip malformed events
      }
    }
  }
};

export const createPolicyCall = async (accessToken: string, policyData: any) => {
  try {
    const data = await apiClient.post(`/policies`, { accessToken, body: policyData });
    return data;
  } catch (error) {
    console.error("Failed to create policy:", error);
    throw error;
  }
};

export const updatePolicyCall = async (accessToken: string, policyId: string, policyData: any) => {
  try {
    const data = await apiClient.put(`/policies/${policyId}`, { accessToken, body: policyData });
    return data;
  } catch (error) {
    console.error("Failed to update policy:", error);
    throw error;
  }
};

export const listPolicyVersions = async (
  accessToken: string,
  policyName: string,
): Promise<{ policy_name: string; versions: any[]; total_count: number }> => {
  try {
    const encodedName = encodeURIComponent(policyName);
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/policies/name/${encodedName}/versions`
      : `/policies/name/${encodedName}/versions`;
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

    return await response.json();
  } catch (error) {
    console.error("Failed to list policy versions:", error);
    throw error;
  }
};

export const createPolicyVersion = async (
  accessToken: string,
  policyName: string,
  sourcePolicyId?: string | null,
): Promise<any> => {
  try {
    const encodedName = encodeURIComponent(policyName);
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/policies/name/${encodedName}/versions`
      : `/policies/name/${encodedName}/versions`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ source_policy_id: sourcePolicyId ?? undefined }),
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to create policy version:", error);
    throw error;
  }
};

export const updatePolicyVersionStatus = async (
  accessToken: string,
  policyId: string,
  versionStatus: "published" | "production",
): Promise<any> => {
  try {
    return await apiClient.put(`/policies/${policyId}/status`, {
      accessToken,
      body: { version_status: versionStatus },
    });
  } catch (error) {
    console.error("Failed to update policy version status:", error);
    throw error;
  }
};

export const deletePolicyCall = async (accessToken: string, policyId: string) => {
  try {
    const data = await apiClient.delete(`/policies/${policyId}`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to delete policy:", error);
    throw error;
  }
};

export const getPolicyInfo = async (accessToken: string, policyId: string) => {
  try {
    const data = await apiClient.get(`/policies/${policyId}`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get policy info:", error);
    throw error;
  }
};

// Policy Attachments API Calls

export const getPolicyAttachmentsList = async (accessToken: string) => {
  try {
    const data = await apiClient.get(`/policies/attachments/list`, { accessToken });
    return data;
  } catch (error) {
    console.error("Failed to get policy attachments list:", error);
    throw error;
  }
};

export const createPolicyAttachmentCall = async (accessToken: string, attachmentData: any) => {
  try {
    const data = await apiClient.post(`/policies/attachments`, { accessToken, body: attachmentData });
    return data;
  } catch (error) {
    console.error("Failed to create policy attachment:", error);
    throw error;
  }
};

export const deletePolicyAttachmentCall = async (accessToken: string, attachmentId: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/policies/attachments/${attachmentId}`
      : `/policies/attachments/${attachmentId}`;
    const response = await fetch(url, {
      method: "DELETE",
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
    console.error("Failed to delete policy attachment:", error);
    throw error;
  }
};

export const testPipelineCall = async (
  accessToken: string,
  pipeline: any,
  testMessages: Array<{ role: string; content: string }>,
) => {
  try {
    const data = await apiClient.post(`/policies/test-pipeline`, {
      accessToken,
      body: { pipeline, test_messages: testMessages },
    });
    return data;
  } catch (error) {
    console.error("Failed to test pipeline:", error);
    throw error;
  }
};

export const getResolvedGuardrails = async (accessToken: string, policyId: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/policies/${policyId}/resolved-guardrails`
      : `/policies/${policyId}/resolved-guardrails`;
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
    console.error("Failed to get resolved guardrails:", error);
    throw error;
  }
};

export const resolvePoliciesCall = async (
  accessToken: string,
  context: { team_alias?: string; key_alias?: string; model?: string; tags?: string[] },
) => {
  try {
    return await apiClient.post(`/policies/resolve`, { accessToken, body: context });
  } catch (error) {
    console.error("Failed to resolve policies:", error);
    throw error;
  }
};

export const estimateAttachmentImpactCall = async (accessToken: string, attachmentData: any) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/policies/attachments/estimate-impact`
      : `/policies/attachments/estimate-impact`;
    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(attachmentData),
    });

    if (!response.ok) {
      const errorData = await response.json();
      const errorMessage = deriveErrorMessage(errorData);
      handleError(errorMessage);
      throw new Error(errorMessage);
    }

    return await response.json();
  } catch (error) {
    console.error("Failed to estimate attachment impact:", error);
    throw error;
  }
};
