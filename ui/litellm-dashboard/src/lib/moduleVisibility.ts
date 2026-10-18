const PAGE_ALIASES: Record<string, string> = {
  "api-reference": "api_ref",
  playground: "llm-playground",
  "old-usage": "usage",
};

export const DEFAULT_DISABLED_PAGES: readonly string[] = [
  "llm-playground",
  "agents",
  "workflows",
  "memory",
  "mcp-servers",
  "guardrails",
  "policies",
  "search-tools",
  "vector-stores",
  "tool-policies",
  "guardrails-monitor",
  "teams",
  "projects",
  "users",
  "organizations",
  "access-groups",
  "budgets",
  "api_ref",
  "model-hub-table",
  "learning-resources",
  "caching",
  "prompts",
  "transform-request",
  "tag-management",
  "usage",
];

export const MODULE_OPTIONS: readonly { page: string; label: string; group: string }[] = [
  { page: "llm-playground", label: "试用场", group: "AI 网关" },
  { page: "mcp-servers", label: "MCP 服务器", group: "AI 网关" },
  { page: "guardrails", label: "防护栏", group: "AI 网关" },
  { page: "policies", label: "策略", group: "AI 网关" },
  { page: "agents", label: "Agents", group: "智能体" },
  { page: "workflows", label: "Workflow Runs", group: "智能体" },
  { page: "memory", label: "Memory", group: "智能体" },
  { page: "search-tools", label: "Search Tools", group: "工具" },
  { page: "vector-stores", label: "Vector Stores", group: "工具" },
  { page: "tool-policies", label: "Tool Policies", group: "工具" },
  { page: "guardrails-monitor", label: "防护栏监控", group: "可观测性" },
  { page: "teams", label: "Teams", group: "访问控制" },
  { page: "projects", label: "Projects", group: "访问控制" },
  { page: "users", label: "Internal Users", group: "访问控制" },
  { page: "organizations", label: "Organizations", group: "访问控制" },
  { page: "access-groups", label: "Access Groups", group: "访问控制" },
  { page: "budgets", label: "Budgets", group: "访问控制" },
  { page: "api_ref", label: "API Reference", group: "开发者工具" },
  { page: "model-hub-table", label: "AI Hub", group: "开发者工具" },
  { page: "learning-resources", label: "Learning Resources", group: "开发者工具" },
  { page: "caching", label: "Response Cache", group: "开发者工具" },
  { page: "prompts", label: "Prompts", group: "开发者工具" },
  { page: "transform-request", label: "API Playground", group: "开发者工具" },
  { page: "tag-management", label: "Tag Management", group: "开发者工具" },
  { page: "usage", label: "Old Usage", group: "开发者工具" },
];

const PROTECTED_PAGES = new Set(["settings"]);

export const canonicalPageKey = (page: string): string => PAGE_ALIASES[page] ?? page;

export const isPageDisabled = (page: string, disabledPages: readonly string[]): boolean => {
  const key = canonicalPageKey(page);
  return !PROTECTED_PAGES.has(key) && disabledPages.includes(key);
};

export const disabledPagesOrDefault = (value: unknown): readonly string[] =>
  Array.isArray(value) && value.every((item): item is string => typeof item === "string")
    ? value
    : DEFAULT_DISABLED_PAGES;

export const setPageEnabled = (
  disabledPages: readonly string[],
  page: string,
  enabled: boolean,
): readonly string[] => {
  if (enabled) return disabledPages.filter((item) => item !== page);
  return disabledPages.includes(page) ? disabledPages : [...disabledPages, page];
};
