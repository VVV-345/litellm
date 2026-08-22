export const providerModelForSelectedModel = (model: string, providerPrefix: string): string =>
  model.includes("/") ? model : `${providerPrefix}/${model}`;
