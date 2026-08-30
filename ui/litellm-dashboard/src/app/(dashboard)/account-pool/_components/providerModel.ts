const legacyForwardingProviderAliases: Readonly<Record<string, string>> = {
  openai_compatible: "openai",
  new_api: "openai",
  glm_official: "zai",
  lmu_static_metadata: "openai",
};

export const normalizeForwardingProvider = (provider: string): string =>
  legacyForwardingProviderAliases[provider] ?? provider;

export const providerModelForSelectedModel = (model: string, providerPrefix: string): string =>
  `${providerPrefix}/${model}`;
