/** 只读价格目录。供应商原币参考价与运行时部署费率不参与路由或计费。 */
export type PriceRow = {
  model: string;
  names: string[];
  currency: string;
  region: string;
  tier: string;
  input: number | null;
  cache: number | null;
  output: number | null;
  source: "catalog" | "deployment" | "official";
  details: string;
};

const million = (n: unknown) => (typeof n === "number" && Number.isFinite(n) && n >= 0 ? n * 1e6 : null);
const qualified = (provider: string, model: string) =>
  model.startsWith(provider + "/") ? model : provider + "/" + model;
export const officialPricePages = {
  CN: "https://docs.bigmodel.cn/cn/guide/start/pricing",
  International: "https://docs.z.ai/guides/overview/pricing",
};
const glm = (
  model: string,
  region: keyof typeof officialPricePages,
  tier: string,
  [input, cache, output]: readonly [number, number, number],
): PriceRow => ({
  model: qualified(region === "CN" ? "bigmodel.cn" : "zai", model),
  names: [],
  region,
  currency: region === "CN" ? "CNY" : "USD",
  tier,
  input,
  cache,
  output,
  source: "official",
  details: "",
});
const glmReferences = [
  glm("glm-5.3", "CN", "1M", [8, 2, 28]),
  glm("glm-5.3-flash", "CN", "1M", [0.8, 0.23, 2.8]),
  glm("glm-5.3-flashx", "CN", "1M", [2, 0.57, 7]),
  glm("glm-5.3-flashx-legal", "CN", "1M", [2, 0.57, 7]),
  glm("glm-5.2", "CN", "1M", [8, 2, 28]),
  glm("glm-5.1", "CN", "input <32K", [6, 1.3, 24]),
  glm("glm-5.1", "CN", "input ≥32K", [8, 2, 28]),
  glm("glm-5", "CN", "input <32K", [4, 1, 18]),
  glm("glm-5", "CN", "input ≥32K", [6, 1.5, 22]),
  glm("glm-4.7", "CN", "input <32K, output <0.2K", [2, 0.4, 8]),
  glm("glm-4.7", "CN", "input <32K, output ≥0.2K", [3, 0.6, 14]),
  glm("glm-4.7", "CN", "32K ≤ input <200K", [4, 0.8, 16]),
  glm("glm-5.3", "International", "", [1.4, 0.26, 4.4]),
  glm("glm-5.3-flash", "International", "", [0.15, 0.03, 0.5]),
  glm("glm-5.3-flashx", "International", "", [0.37, 0.075, 1.25]),
  glm("glm-5.3-flashx-legal", "International", "", [0.37, 0.075, 1.25]),
  glm("glm-5.2", "International", "", [1.4, 0.26, 4.4]),
];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);
const baseRates = new Set(["input_cost_per_token", "cache_read_input_token_cost", "output_cost_per_token"]);
const referenceKey = (row: PriceRow) =>
  JSON.stringify([row.model, row.currency, row.region, row.input, row.cache, row.output]);
function priceIdentity(name: string, value: Record<string, unknown>) {
  const deployment = value.id === name || value.managed_by === "account_pool";
  const base = typeof value.base_model === "string" ? value.base_model : "";
  // Some deployment-ID entries identify the provider only through a qualified base_model.
  const baseProvider = base.includes("/") ? base.split("/", 1)[0] : "";
  const provider = typeof value.litellm_provider === "string" ? value.litellm_provider : baseProvider;
  if (!provider && !deployment && !base) return null;
  const model = provider ? qualified(provider, base || name) : base || name;
  return { deployment, model, provider };
}

export function modelPriceCatalog(map: Record<string, unknown> = {}): PriceRow[] {
  const rows = new Map<string, PriceRow>();
  const present = new Set<string>();
  for (const [name, value] of Object.entries(map)) {
    if (!isRecord(value) || name === "sample_spec") continue;
    const identity = priceIdentity(name, value);
    if (!identity) continue;
    const { deployment, model, provider } = identity;
    const region =
      [value.region_name, value.region].find((v): v is string => typeof v === "string") ??
      (provider === "zai" ? "International" : "");
    const extras = Object.keys(value)
      .sort()
      .filter(
        (key) =>
          !baseRates.has(key) &&
          /_cost(?:_per_|$|_above_|_priority|_flex)|(?:^|_)(?:tier|tiered_pricing)(?:_|$)|^max_.*tokens$|^supported_regions$|^regional_.*multiplier/.test(
            key,
          ),
      );
    const details = JSON.stringify(Object.fromEntries(extras.map((key) => [key, value[key]])));
    const row: PriceRow = {
      model,
      names: [name],
      region,
      tier: "",
      currency: typeof value.currency === "string" ? value.currency : "USD",
      input: million(value.input_cost_per_token),
      cache: million(value.cache_read_input_token_cost),
      output: million(value.output_cost_per_token),
      source: deployment ? "deployment" : "catalog",
      details: details === "{}" ? "" : details,
    };
    const key = JSON.stringify([
      model,
      provider || name,
      region,
      row.currency,
      value.mode,
      row.source,
      row.input,
      row.cache,
      row.output,
      details,
    ]);
    const existing = rows.get(key);
    if (existing) existing.names.push(name);
    else rows.set(key, row);
    if (!deployment && region === "International" && !extras.some((key) => /tier|above_|below_/.test(key))) {
      present.add(referenceKey(row));
    }
  }
  return [...rows.values(), ...glmReferences.filter((row) => !present.has(referenceKey(row)))].sort(
    (a, b) => a.model.localeCompare(b.model) || a.tier.localeCompare(b.tier),
  );
}

export function deploymentPriceModel(model: string, apiBase?: string, baseModel?: string): string | null {
  const base = apiBase?.replace(/\/+$/, "");
  const bare = model.replace(/^(?:openai|zai)\//, "");
  if (base === "https://open.bigmodel.cn/api/paas/v4") return qualified("bigmodel.cn", bare);
  if (base === "https://api.z.ai/api/paas/v4") return qualified("zai", bare);
  if (base) return null;
  if (baseModel?.includes("/")) return baseModel;
  return model.includes("/") ? model : null;
}
