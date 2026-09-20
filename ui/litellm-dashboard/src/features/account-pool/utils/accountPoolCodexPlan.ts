const normalizeAuthFilePlan = (value: string | null | undefined): "prolite" | "promax" | undefined => {
  const normalized = (value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[_\s]+/g, "-");
  if (["prolite", "pro-lite", "pro-5x", "codex-pro-5x"].includes(normalized)) return "prolite";
  if (["promax", "pro-max", "pro-20x", "codex-pro-20x"].includes(normalized)) return "promax";
  return undefined;
};

export const accountPoolPlanLabel = (
  planType: string | null | undefined,
  authFilePlanType: string | null | undefined,
): string => {
  const normalized = (planType ?? "").trim().toLowerCase();
  if (!normalized) return "-";
  if (normalized.includes("business")) return "BUSINESS";
  if (normalized.includes("enterprise")) return "ENTERPRISE";
  if (normalized.includes("team")) return "TEAM";
  if (normalized.includes("plus")) return "PLUS";
  if (normalized.includes("pro")) {
    return normalizeAuthFilePlan(authFilePlanType ?? planType) === "prolite" ? "PRO 5x" : "PRO 20x";
  }
  if (normalized.includes("free")) return "FREE";
  if (normalized.includes("api")) return "API";
  return planType!.trim().toUpperCase();
};
