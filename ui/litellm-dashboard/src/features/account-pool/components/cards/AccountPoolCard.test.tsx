import i18n from "@/i18n";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolCard } from "./AccountPoolCard";
import type { ErrorStats, PolicyView } from "../../api/AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolProxyGateway } from "../../utils/AccountPoolTypes";

vi.mock("../../api/AccountPoolApi", () => ({
  updateAccountPoolEnvironment: vi.fn(),
}));

const renderCard = (
  overrides: Partial<AccountPoolEnvironment> = {},
  proxyGateway?: AccountPoolProxyGateway,
  policy?: PolicyView,
  requestStats?: ErrorStats,
) => {
  const actions = {
    onRefreshCredentials: vi.fn(),
    onVerifyAccess: vi.fn(),
    onQuotaReset: vi.fn(),
    onViewLogs: vi.fn(),
  };
  const environment = {
    id: "env-claude-1",
    version: 1,
    name: "Claude account",
    provider: "openai",
    channel: "cliproxyapi",
    supplier: "anthropic_claude",
    authorization_flow: "browser_oauth",
    status: "ready",
    configuration_pending: false,
    enabled: true,
    manual_cooldown: false,
    concurrency_limit: 2,
    proxy_mode: "default_gateway",
    proxy_profile_id: null,
    available_models: ["claude-model"],
    enabled_models: ["claude-model"],
    quota: { observed_at: null, plan_type: null, windows: [], balances: [] },
    model_quotas: [],
    cooldown_until: null,
    automatic_cooldown: false,
    last_error: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as AccountPoolEnvironment;

  render(
    <AccountPoolCard
      environment={environment}
      requestStats={requestStats}
      proxyGateway={proxyGateway}
      onConfigure={vi.fn()}
      onEnabledChange={vi.fn()}
      onAuthorize={vi.fn()}
      onDelete={vi.fn()}
      {...actions}
      onManageKey={vi.fn()}
      policy={policy}
    />,
  );
  return { actions, environment };
};

const createCostStats = (total_cost_usd: number | null, known_cost_requests: number): ErrorStats => ({
  statistics_source: "litellm",
  card_id: "env-claude-1",
  account_id: "env-claude-1",
  model: "claude-model",
  total_requests: 1,
  succeeded_requests: 1,
  failed_requests: 0,
  retried_requests: 0,
  input_tokens: 100,
  output_tokens: 50,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
  cache_rate: null,
  known_cost_requests,
  total_cost_usd,
  average_duration_ms: 100,
  recent_errors: [],
});

describe("AccountPoolCard", () => {
  it("shows every Codex quota window with its own remaining meter and reset, including zero credits", () => {
    renderCard({
      supplier: "openai_codex",
      quota: {
        observed_at: "2026-09-15T08:00:00Z",
        refresh_attempted_at: "2026-09-15T08:05:00Z",
        source: "provider_api",
        plan_type: "chatgptplusplan",
        refresh_status: "complete",
        reset_credits_available: 0,
        windows: [
          {
            name: "5 hour",
            used_percent: 3,
            remaining_percent: 97,
            window_minutes: 300,
            resets_at: "2090-09-15T13:04:00Z",
          },
          {
            name: "Weekly",
            used_percent: 22,
            remaining_percent: 78,
            window_minutes: 10080,
            resets_at: "2090-09-22T08:04:00Z",
          },
          {
            name: "gpt-reserve Weekly",
            used_percent: 100,
            remaining_percent: 0,
            window_minutes: 10080,
            resets_at: null,
          },
        ],
        balances: [],
      },
    });
    expect(screen.getByText("Plus")).toBeInTheDocument();
    expect(screen.getByText(/可用重置次数.*0/)).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "5 小时额度" })).toHaveAttribute("aria-valuenow", "97");
    expect(screen.getByRole("meter", { name: "周额度" })).toHaveAttribute("aria-valuenow", "78");
    expect(screen.getByRole("meter", { name: "gpt-reserve 周额度" })).toHaveAttribute("aria-valuenow", "0");
    expect(within(screen.getByRole("group", { name: "5 小时额度" })).getByText(/09.*15/)).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "周额度" })).getByText(/09.*22/)).toBeInTheDocument();
    expect(screen.queryByText("chatgptplusplan")).not.toBeInTheDocument();
    expect(screen.getByText(/供应商主动接口|Provider API/i)).toBeInTheDocument();
    expect(screen.getByText(/额度出口：默认网关|Quota route: Default gateway/i)).toBeInTheDocument();
    expect(screen.getByText(/最近尝试刷新于|Last refresh attempt/i)).toBeInTheDocument();
  });

  it("does not invent quota or reset credits when the provider has not returned them", () => {
    renderCard({ supplier: "openai_codex" });
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    expect(screen.getByText("尚未观测")).toBeInTheDocument();
    expect(screen.getByText(/可用重置次数.*暂无数据/)).toBeInTheDocument();
  });

  it("allows choosing a proxy after the initial authorization fails", () => {
    renderCard({ status: "error", available_models: [], enabled_models: [] });
    expect(screen.getByRole("button", { name: /配置|Configure/i })).toBeEnabled();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-disabled", "true");
  });

  it("shows the shared port and selected node", () => {
    const proxyGateway: AccountPoolProxyGateway = {
      port: 7891,
      profile_id: "clash-gateway-7891",
      name: "Clash 端口 7891",
      proxy_url: "http://host:7891",
      current_node: "美国01",
    };
    renderCard({ channel: "cliproxyapi", proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" }, proxyGateway);

    expect(screen.getByText("端口 7891 · 美国01")).toBeInTheDocument();
  });

  it("shows the selected port when gateway details are unavailable", () => {
    renderCard({ proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" });
    expect(screen.getByText("端口 7891 · 当前节点未知")).toBeInTheDocument();
  });

  it("shows translated channel and supplier labels instead of a static OpenAI label", () => {
    renderCard();

    expect(screen.getByText(/CLIProxyAPI · Anthropic Claude/)).toBeInTheDocument();
    expect(screen.queryByText("OpenAI Codex")).not.toBeInTheDocument();
  });

  it("shows the Kimi supplier label for a Kimi environment", () => {
    renderCard({ supplier: "kimi", enabled_models: ["kimi-model"], available_models: ["kimi-model"] });

    expect(screen.getByText(/CLIProxyAPI · Kimi/)).toBeInTheDocument();
    expect(screen.getByText("设备 ID 认证后自动维护")).toBeInTheDocument();
  });

  it("shows Grok Code access returned by the provider", () => {
    renderCard({
      supplier: "xai",
      quota: { observed_at: null, plan_type: "SuperGrok", has_grok_code_access: true, windows: [], balances: [] },
    });

    expect(screen.getByText(/Grok Code 权限|Grok Code access/i)).toBeInTheDocument();
    expect(screen.getAllByText(/^可用$|^Available$/i).length).toBeGreaterThan(1);
  });

  it("shows the authenticated provider setting summary on a card", () => {
    renderCard({}, undefined, {
      card_id: "env-claude-1",
      version: 1,
      policy: {
        tags: [],
        group: "",
        account_ids: [],
        excluded_models: [],
        model_aliases: [],
        claude: {
          fingerprint_profile: "claude-code-cli",
          cloak_mode: "auto",
          rebuild_mid_system_message: false,
        },
      },
      runtime_status: "partial",
      metadata_status: "saved",
    } as PolicyView);

    expect(screen.getByText(/Claude Code CLI/)).toBeInTheDocument();
  });

  it("keeps the configure button enabled while awaiting authorization and shows the proxy hint", () => {
    renderCard({
      status: "awaiting_authorization",
      available_models: [],
      enabled_models: [],
    });

    expect(screen.getByRole("button", { name: /配置|Configure/i })).toBeEnabled();
    expect(screen.getByText(/先.*代理|proxy before authorizing/i)).toBeInTheDocument();
  });

  it("links the inherited policy badge to the policy center", () => {
    renderCard();

    expect(screen.getByRole("link", { name: "继承全局" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/ui\/policy-center\/?$/),
    );
  });

  it("shows the independent policy version in the card badge", () => {
    renderCard({}, undefined, { card_id: "env-claude-1", version: 4, policy: {} } as PolicyView);

    expect(screen.getByRole("link", { name: "独立配置 v4" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/ui\/policy-center\/?$/),
    );
  });

  it("shows the per-card token cost from LiteLLM request statistics", () => {
    renderCard({ spend_usd: 0 }, undefined, undefined, createCostStats(12.34, 1));

    expect(screen.getByTestId("account-pool-card-env-claude-1")).toHaveTextContent("已用 $12.34");
  });

  it("shows positive sub-cent spend instead of rounding it to zero", () => {
    renderCard({}, undefined, undefined, createCostStats(0.00042, 1));

    expect(screen.getByTestId("account-pool-card-env-claude-1")).toHaveTextContent("已用 $0.00042");
  });

  it("does not show an environment spend as token cost when request cost is unknown", () => {
    renderCard({ spend_usd: 12.34 }, undefined, undefined, createCostStats(0, 0));

    expect(screen.getByTestId("account-pool-card-env-claude-1")).toHaveTextContent("已用 暂无可信金额");
    expect(screen.queryByText("已用 $12.34")).not.toBeInTheDocument();
    expect(screen.queryByText("已用 $0.00")).not.toBeInTheDocument();
  });
});

it("keeps credential warnings visible and displays backoff alongside the next schedule", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_codex",
    credential_refresh: {
      last_result: "failed",
      schedule_reason: "backoff",
      next_refresh_at: "2090-01-01T00:00:00Z",
      running: false,
      save_pending: false,
    },
  });
  expect(screen.getByRole("button", { name: "刷新凭据" })).toBeVisible();
  expect(screen.getByText("刷新失败")).toBeVisible();
  expect(screen.getByText(/退避中/)).toBeInTheDocument();
});

it("keeps egress, request data and small recovery/log buttons visible without expanding details", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard(
    { supplier: "openai_codex", proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" },
    undefined,
    undefined,
    { ...createCostStats(1, 1), total_requests: 1200, input_tokens: 1500000 },
  );
  expect(screen.getByText("端口 7891 · 当前节点未知")).toBeVisible();
  expect(screen.getByText("1.2K")).toBeVisible();
  expect(screen.getByRole("button", { name: /额度重置/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /卡片日志/ })).toBeVisible();
});

it("groups all Codex recovery and log actions in one row and keeps their handlers", async () => {
  await i18n.changeLanguage("zh-CN");
  const { actions, environment } = renderCard({ supplier: "openai_codex" }, undefined, {
    card_id: "env-claude-1",
    version: 1,
    policy: { codex: { allow_valid_access_after_refresh_failure: true } },
  } as PolicyView);
  const row = screen.getByRole("group", { name: "操作" });
  expect(row).toHaveClass("flex", "flex-wrap");
  expect(within(row).getAllByRole("button")).toHaveLength(4);
  for (const [label, handler] of [
    ["刷新凭据", actions.onRefreshCredentials],
    ["检查可用性", actions.onVerifyAccess],
    ["额度重置与验证", actions.onQuotaReset],
    ["查看卡片日志", actions.onViewLogs],
  ] as const) {
    fireEvent.click(within(row).getByRole("button", { name: label }));
    expect(handler).toHaveBeenCalledExactlyOnceWith(environment);
  }
});

it("keeps card logs in the action row for non-Codex cards without Codex recovery actions", async () => {
  await i18n.changeLanguage("zh-CN");
  const { actions, environment } = renderCard({ supplier: "kimi" });
  const row = screen.getByRole("group", { name: "操作" });
  expect(within(row).getAllByRole("button")).toHaveLength(1);
  fireEvent.click(within(row).getByRole("button", { name: "查看卡片日志" }));
  expect(actions.onViewLogs).toHaveBeenCalledExactlyOnceWith(environment);
});

it("shows GLM plan quota and offers provider reset and verification", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_compatible",
    provider_family: "glm",
    display_quota: {
      plan_type: "Pro",
      windows: [
        {
          name: "5 hour",
          used_percent: 64,
          remaining_percent: 36,
          window_minutes: 300,
          resets_at: "2090-01-01T00:00:00Z",
        },
      ],
      balances: [],
    },
  });
  expect(screen.getByText("套餐额度")).toBeVisible();
  expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "36");
  expect(screen.getByRole("button", { name: "额度重置与验证" })).toBeVisible();
});

it("shows an unchecked GLM plan as not fetched rather than zero", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({ supplier: "openai_compatible", provider_family: "glm" });
  expect(screen.getByText("套餐额度未获取")).toBeVisible();
  expect(screen.queryByRole("meter")).not.toBeInTheDocument();
});

it("offers only the official OpenCode workspace billing link, without a zero balance", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({ supplier: "openai_compatible", provider_family: "opencode" });
  expect(screen.getByRole("link", { name: "打开 OpenCode 账单" })).toHaveAttribute(
    "href",
    "https://opencode.ai/console/login",
  );
  expect(screen.getByText("工作区余额未获取")).toBeVisible();
  expect(screen.queryByRole("meter")).not.toBeInTheDocument();
});

it("shows the three OpenCode Go plan windows and reset times, not a wallet balance", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_compatible",
    provider_family: "opencode",
    display_quota: {
      plan_type: "OpenCode Go",
      refresh_status: "complete",
      windows: [
        {
          name: "5 hour",
          used_percent: 10,
          remaining_percent: 90,
          window_minutes: 300,
          resets_at: "2090-01-01T01:00:00Z",
        },
        {
          name: "Weekly",
          used_percent: 100,
          remaining_percent: 0,
          window_minutes: 10080,
          resets_at: "2090-01-02T00:00:00Z",
        },
        {
          name: "Monthly",
          used_percent: 24,
          remaining_percent: 76,
          window_minutes: 43200,
          resets_at: "2090-01-31T00:00:00Z",
        },
      ],
      balances: [],
    },
  });
  for (const [label, value] of [
    ["5 小时额度", "90"],
    ["周额度", "0"],
    ["月额度", "76"],
  ]) {
    const window = within(screen.getByRole("group", { name: label }));
    expect(window.getByRole("meter")).toHaveAttribute("aria-valuenow", value);
    expect(window.getByText(/^重置时间 (?!-$)/)).toBeVisible();
  }
  expect(screen.getByRole("link", { name: "打开 OpenCode 账单" })).toBeVisible();
  expect(screen.queryByText("工作区余额未获取")).not.toBeInTheDocument();
});

it("shows a failed OpenCode quota query even before its plan has been identified", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_compatible",
    provider_family: "opencode",
    display_quota: {
      plan_type: null,
      refresh_attempted_at: "2090-01-01T00:00:00Z",
      refresh_status: "failed",
      refresh_error: "OpenCode Go 套餐额度未获取（HTTP 401）",
      windows: [],
      balances: [],
    },
  });
  expect(screen.getByRole("status")).toHaveTextContent("OpenCode Go 套餐额度未获取（HTTP 401）");
  expect(screen.getByText("套餐额度未获取")).toBeVisible();
  expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "打开 OpenCode 账单" })).toBeVisible();
});

it("labels the optional GLM monthly window as MCP, not model quota", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_compatible",
    provider_family: "glm",
    display_quota: {
      windows: [{ name: "MCP Monthly", used_percent: 20, remaining_percent: 80, window_minutes: 43200 }],
      balances: [],
    },
  });
  expect(screen.getByRole("meter", { name: "MCP 月额度" })).toBeVisible();
  expect(screen.queryByRole("meter", { name: "月额度" })).not.toBeInTheDocument();
});

it("shows an uncertain refresh as paused without claiming the access token was rejected", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_codex",
    credential_refresh: {
      last_result: "unknown_outcome",
      schedule_reason: "refresh_outcome_unknown",
      running: false,
      save_pending: false,
    },
  });
  expect(screen.getByText("刷新结果未知，已暂停重试；有效访问令牌仍可验证使用")).toBeVisible();
  expect(screen.queryByText("上游拒绝，需重新授权")).not.toBeInTheDocument();
});

it.each([false, true])("keeps the Codex policy summary when identity confusion is %s", async (enabled) => {
  await i18n.changeLanguage("zh-CN");
  const policy = {
    card_id: "env-claude-1",
    version: 1,
    policy: {
      codex: {
        identity_fingerprint_mode: "off",
        identity_confuse: enabled,
        cli_only: false,
        allow_app_server: false,
        disable_codex_cloaking: false,
      },
    },
  } as PolicyView;
  renderCard({ supplier: "openai_codex" }, undefined, policy);
  fireEvent.click(screen.getByText(i18n.t("accountPool.credential.details")));
  expect(screen.getByText(i18n.t("accountPool.policy.providerSummary"))).toBeVisible();
  expect(
    screen.getByText(i18n.t(enabled ? "accountPool.policy.identity_confuse" : "accountPool.policy.options.disabled")),
  ).toBeVisible();
});

it.each([0, 700])("shows the provider prepaid balance %i on the card, retaining its unit", async (amount) => {
  await i18n.changeLanguage("zh-CN");
  renderCard({ supplier: "xai", quota: { windows: [], balances: [], prepaid_balance: amount } });
  expect(screen.getByText("预付余额")).toBeVisible();
  expect(screen.getByText(`${amount} 美分`)).toBeVisible();
});

it("shows a named zero credit balance and provider minimum without inventing another wallet", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "google_antigravity",
    quota: {
      windows: [],
      balances: [{ name: "GOOGLE_ONE_AI", available: 0, minimum_required: 50, unit: "credits" }],
    },
  });
  expect(screen.getByText("GOOGLE ONE AI 可用余额")).toBeVisible();
  expect(screen.getByText("0 点数")).toBeVisible();
  expect(screen.getByText("最低使用门槛：50")).toBeVisible();
  expect(screen.queryByText("预付余额")).not.toBeInTheDocument();
});

it("keeps a failed plan snapshot visible with an explicit stale label", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({
    supplier: "openai_compatible",
    provider_family: "glm",
    display_quota: {
      observed_at: "2026-01-01T00:00:00Z",
      source: "stored_cache",
      refresh_status: "failed",
      refresh_attempted_at: "2026-01-01T00:05:00Z",
      windows: [{ name: "5 hour", used_percent: 100, remaining_percent: 0, window_minutes: 300 }],
      balances: [],
    },
  });
  expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "0");
  expect(screen.getByText("快照已过期，等待刷新")).toBeVisible();
  expect(screen.getByText("主动刷新失败，已回退缓存")).toBeVisible();
});


it("shows saved-but-unverified OAuth credentials instead of silently looking routable", () => {
  renderCard({ supplier: "openai_codex", oauth_verification_pending: true, auth_file_bound: true });
  expect(screen.getByText(/凭据已保存，等待授权验收，暂不接单/)).toBeVisible();
});

it("displays live in-flight concurrency alongside the configured concurrency limit", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({ in_flight: 3, concurrency_limit: 10 });
  expect(screen.getByText("3 / 10")).toBeVisible();
});

it("displays live in-flight concurrency alongside unlimited concurrency when limit is zero", async () => {
  await i18n.changeLanguage("zh-CN");
  renderCard({ in_flight: 2, concurrency_limit: 0 });
  expect(screen.getByText(`2 / ${i18n.t("accountPool.config.unlimited")}`)).toBeVisible();
});
