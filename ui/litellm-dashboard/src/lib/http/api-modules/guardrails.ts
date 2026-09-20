// 护栏管理接口；共用客户端状态，保持既有请求契约。
import { proxyBaseUrl, globalLitellmHeaderName, handleError, apiClient } from "./clientState";
import { deriveErrorMessage } from "@/lib/http/client";

export const getGuardrailsList = async (accessToken: string) => {
  try {
    const v2Url = proxyBaseUrl ? `${proxyBaseUrl}/v2/guardrails/list` : `/v2/guardrails/list`;
    const response = await fetch(v2Url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(`v2 guardrails/list returned ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    try {
      const v1Url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/list` : `/guardrails/list`;
      const fallbackResponse = await fetch(v1Url, {
        method: "GET",
        headers: {
          [globalLitellmHeaderName]: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
      });

      if (!fallbackResponse.ok) {
        const errorData = await fallbackResponse.json();
        const errorMessage = deriveErrorMessage(errorData);
        handleError(errorMessage);
        throw new Error(errorMessage);
      }

      return await fallbackResponse.json();
    } catch (fallbackError) {
      console.error("Failed to get guardrails list:", fallbackError);
      throw fallbackError;
    }
  }
};

// Team guardrail submissions (admin)
export interface GuardrailSubmissionItem {
  guardrail_id: string;
  guardrail_name: string;
  status: string; // "pending_review" | "active" | "rejected"
  team_id?: string | null;
  team_guardrail?: boolean; // true when submitted via team (team_id set)
  litellm_params?: Record<string, unknown> | null;
  guardrail_info?: Record<string, unknown> | null;
  submitted_by_user_id?: string | null;
  submitted_by_email?: string | null;
  submitted_at?: string | null;
  reviewed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface GuardrailSubmissionSummary {
  total: number;
  pending_review: number;
  active: number;
  rejected: number;
}

interface ListGuardrailSubmissionsResponse {
  submissions: GuardrailSubmissionItem[];
  summary: GuardrailSubmissionSummary;
}

export const listGuardrailSubmissions = async (
  accessToken: string,
  params?: { status?: string; team_id?: string; team_guardrail?: boolean; search?: string },
): Promise<ListGuardrailSubmissionsResponse> => {
  return apiClient.get<ListGuardrailSubmissionsResponse>(`/guardrails/submissions`, {
    accessToken,
    query: {
      ...(params?.status ? { status: params.status } : {}),
      ...(params?.team_id ? { team_id: params.team_id } : {}),
      ...(params?.team_guardrail !== undefined ? { team_guardrail: params.team_guardrail } : {}),
      ...(params?.search ? { search: params.search } : {}),
    },
  });
};

export const approveGuardrailSubmission = async (
  accessToken: string,
  guardrailId: string,
): Promise<{ guardrail_id: string; status: string; message: string }> => {
  return apiClient.post<{ guardrail_id: string; status: string; message: string }>(
    `/guardrails/submissions/${encodeURIComponent(guardrailId)}/approve`,
    { accessToken },
  );
};

export const rejectGuardrailSubmission = async (
  accessToken: string,
  guardrailId: string,
): Promise<{ guardrail_id: string; status: string; message: string }> => {
  return apiClient.post<{ guardrail_id: string; status: string; message: string }>(
    `/guardrails/submissions/${encodeURIComponent(guardrailId)}/reject`,
    { accessToken },
  );
};

// Guardrails / Policies usage (dashboard)
export const getGuardrailsUsageOverview = async (accessToken: string, startDate?: string, endDate?: string) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/usage/overview` : `/guardrails/usage/overview`;
    const params = new URLSearchParams();
    if (startDate) params.append("start_date", startDate);
    if (endDate) params.append("end_date", endDate);
    if (params.toString()) url += `?${params.toString()}`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(deriveErrorMessage(errorData));
    }
    return response.json();
  } catch (error) {
    console.error("Failed to get guardrails usage overview:", error);
    throw error;
  }
};

export const getGuardrailsUsageDetail = async (
  accessToken: string,
  guardrailId: string,
  startDate?: string,
  endDate?: string,
) => {
  try {
    let url = proxyBaseUrl
      ? `${proxyBaseUrl}/guardrails/usage/detail/${encodeURIComponent(guardrailId)}`
      : `/guardrails/usage/detail/${encodeURIComponent(guardrailId)}`;
    const params = new URLSearchParams();
    if (startDate) params.append("start_date", startDate);
    if (endDate) params.append("end_date", endDate);
    if (params.toString()) url += `?${params.toString()}`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(deriveErrorMessage(errorData));
    }
    return response.json();
  } catch (error) {
    console.error("Failed to get guardrails usage detail:", error);
    throw error;
  }
};

export const getGuardrailsUsageLogs = async (
  accessToken: string,
  options: {
    guardrailId?: string;
    policyId?: string;
    page?: number;
    pageSize?: number;
    action?: string;
    startDate?: string;
    endDate?: string;
  },
) => {
  try {
    let url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/usage/logs` : `/guardrails/usage/logs`;
    const params = new URLSearchParams();
    if (options.guardrailId) params.append("guardrail_id", options.guardrailId);
    if (options.policyId) params.append("policy_id", options.policyId);
    if (options.page != null) params.append("page", String(options.page));
    if (options.pageSize != null) params.append("page_size", String(options.pageSize));
    if (options.action) params.append("action", options.action);
    if (options.startDate) params.append("start_date", options.startDate);
    if (options.endDate) params.append("end_date", options.endDate);
    if (params.toString()) url += `?${params.toString()}`;
    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(deriveErrorMessage(errorData));
    }
    return response.json();
  } catch (error) {
    console.error("Failed to get guardrails usage logs:", error);
    throw error;
  }
};

export const createGuardrailCall = async (accessToken: string, guardrailData: any) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails` : `/guardrails`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        guardrail: guardrailData,
      }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to create guardrail:", error);
    throw error;
  }
};

export const deleteGuardrailCall = async (accessToken: string, guardrailId: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/${guardrailId}` : `/guardrails/${guardrailId}`;

    const response = await fetch(url, {
      method: "DELETE",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error(errorData);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to delete guardrail:", error);
    throw error;
  }
};

export const getGuardrailUISettings = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/guardrails/ui/add_guardrail_settings`
      : `/guardrails/ui/add_guardrail_settings`;

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
      throw new Error("Failed to get guardrail UI settings");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get guardrail UI settings:", error);
    throw error;
  }
};

export const getGuardrailProviderSpecificParams = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/guardrails/ui/provider_specific_params`
      : `/guardrails/ui/provider_specific_params`;

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
      throw new Error("Failed to get guardrail provider specific parameters");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get guardrail provider specific parameters:", error);
    throw error;
  }
};

export const getCategoryYaml = async (accessToken: string, categoryName: string) => {
  try {
    // URL encode the category name to handle special characters
    const encodedCategoryName = encodeURIComponent(categoryName);
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/guardrails/ui/category_yaml/${encodedCategoryName}`
      : `/guardrails/ui/category_yaml/${encodedCategoryName}`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      console.error(`Failed to get category YAML. Status: ${response.status}, Error:`, errorData);
      handleError(errorData);
      throw new Error(`Failed to get category YAML: ${response.status} ${errorData}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get category YAML:", error);
    throw error;
  }
};

export const getMajorAirlines = async (accessToken: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/ui/major_airlines` : `/guardrails/ui/major_airlines`;

    const response = await fetch(url, {
      method: "GET",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const errorData = await response.text();
      console.error(`Failed to get major airlines. Status: ${response.status}, Error:`, errorData);
      handleError(errorData);
      throw new Error(`Failed to get major airlines: ${response.status} ${errorData}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get major airlines:", error);
    throw error;
  }
};

export const getGuardrailInfo = async (accessToken: string, guardrailId: string) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/${guardrailId}/info` : `/guardrails/${guardrailId}/info`;

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
      throw new Error("Failed to get guardrail info");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to get guardrail info:", error);
    throw error;
  }
};

export const updateGuardrailCall = async (
  accessToken: string,
  guardrailId: string,
  updateData: {
    guardrail_name?: string;
    default_on?: boolean;
    guardrail_info?: Record<string, any>;
    litellm_params?: Record<string, any>;
  },
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/${guardrailId}` : `/guardrails/${guardrailId}`;

    const response = await fetch(url, {
      method: "PATCH",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(updateData),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to update guardrail");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to update guardrail:", error);
    throw error;
  }
};

export const applyGuardrail = async (
  accessToken: string,
  guardrailName: string,
  text: string,
  language?: string | null,
  entities?: string[] | null,
  metadata?: Record<string, unknown> | null,
) => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/apply_guardrail` : `/guardrails/apply_guardrail`;

    const requestBody: Record<string, any> = {
      guardrail_name: guardrailName,
      text: text,
    };

    if (language) {
      requestBody.language = language;
    }

    if (entities && entities.length > 0) {
      requestBody.entities = entities;
    }

    if (metadata != null) {
      requestBody.metadata = metadata;
    }

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      const errorData = await response.text();
      let errorMessage = "Failed to apply guardrail";

      try {
        const errorJson = JSON.parse(errorData);
        if (errorJson.error?.message) {
          errorMessage = errorJson.error.message;
        } else if (errorJson.detail) {
          errorMessage = errorJson.detail;
        } else if (errorJson.message) {
          errorMessage = errorJson.message;
        }
      } catch (e) {
        errorMessage = errorData || errorMessage;
      }

      handleError(errorData);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to apply guardrail:", error);
    throw error;
  }
};

interface TestCustomCodeGuardrailRequest {
  custom_code: string;
  test_input: {
    texts: string[];
    images?: string[];
    tools?: Record<string, any>[];
    tool_calls?: Record<string, any>[];
    structured_messages?: Record<string, any>[];
    model?: string;
  };
  input_type?: "request" | "response";
  request_data?: {
    model?: string;
    user_id?: string;
    team_id?: string;
    end_user_id?: string;
    metadata?: Record<string, any>;
  };
}

interface TestCustomCodeGuardrailResponse {
  success: boolean;
  result?: {
    action: "allow" | "block" | "modify";
    reason?: string;
    texts?: string[];
    images?: string[];
    tool_calls?: Record<string, any>[];
    detection_info?: Record<string, any>;
    warning?: string;
  };
  error?: string;
  error_type?: "compilation" | "execution";
}

export const testCustomCodeGuardrail = async (
  accessToken: string,
  request: TestCustomCodeGuardrailRequest,
): Promise<TestCustomCodeGuardrailResponse> => {
  try {
    const url = proxyBaseUrl ? `${proxyBaseUrl}/guardrails/test_custom_code` : `/guardrails/test_custom_code`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const errorData = await response.text();
      let errorMessage = "Failed to test custom code guardrail";

      try {
        const errorJson = JSON.parse(errorData);
        if (errorJson.error?.message) {
          errorMessage = errorJson.error.message;
        } else if (errorJson.detail) {
          errorMessage = errorJson.detail;
        } else if (errorJson.message) {
          errorMessage = errorJson.message;
        }
      } catch (e) {
        errorMessage = errorData || errorMessage;
      }

      handleError(errorData);
      throw new Error(errorMessage);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to test custom code guardrail:", error);
    throw error;
  }
};

export const validateBlockedWordsFile = async (accessToken: string, fileContent: string) => {
  try {
    const url = proxyBaseUrl
      ? `${proxyBaseUrl}/guardrails/validate_blocked_words_file`
      : `/guardrails/validate_blocked_words_file`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        [globalLitellmHeaderName]: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ file_content: fileContent }),
    });

    if (!response.ok) {
      const errorData = await response.text();
      handleError(errorData);
      throw new Error("Failed to validate blocked words file");
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Failed to validate blocked words file:", error);
    throw error;
  }
};
