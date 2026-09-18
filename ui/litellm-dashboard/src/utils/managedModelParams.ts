import { isMaskedSecret } from "./maskedSecretUtils";

const MANAGED_PARAMS = [
  "model",
  "api_base",
  "api_key",
  "custom_llm_provider",
  "litellm_credential_name",
  "max_parallel_requests",
  "num_retries",
  "max_retries",
];

export const editableModelParams = (params: Record<string, unknown>, managed: boolean): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(params).filter(
      ([name, value]) => !isMaskedSecret(value) && !(managed && MANAGED_PARAMS.includes(name)),
    ),
  );
