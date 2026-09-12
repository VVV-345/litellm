import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolCard } from "./AccountPoolCard";
import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment, AccountPoolProxyGateway } from "./AccountPoolTypes";

vi.mock("./AccountPoolApi", () => ({
  updateAccountPoolEnvironment: vi.fn(),
}));

const renderCard = (
  overrides: Partial<AccountPoolEnvironment> = {},
  proxyGateway?: AccountPoolProxyGateway,
  policy?: PolicyView,
) => {
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
    quota: { observed_at: null, plan_type: null, windows: [] },
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
      proxyGateway={proxyGateway}
      onConfigure={vi.fn()}
      onEnabledChange={vi.fn()}
      onAuthorize={vi.fn()}
      onDelete={vi.fn()}
      onManageKey={vi.fn()}
      onManagePolicy={vi.fn()}
      policy={policy}
    />,
  );
};

describe("AccountPoolCard", () => {
  it("allows choosing a proxy after the initial authorization fails", () => {
    renderCard({ status: "error", available_models: [], enabled_models: [] });
    expect(screen.getByRole("button", { name: /配置|Configure/i })).toBeEnabled();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-disabled", "true");
  });

  it.each(["cliproxyapi", "freebuff2api"] as const)("shows the shared port and selected node for %s", (channel) => {
    const proxyGateway: AccountPoolProxyGateway = {
      port: 7891,
      profile_id: "clash-gateway-7891",
      name: "Clash 端口 7891",
      proxy_url: "http://host:7891",
      current_node: "美国01",
    };
    renderCard({ channel, proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" }, proxyGateway);

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

  it("opens the policy editor when the card is double-clicked", () => {
    const onManagePolicy = vi.fn();
    const environment = {
      id: "env-double-click",
      version: 1,
      name: "Double click account",
      provider: "openai",
      channel: "cliproxyapi",
      supplier: "openai_codex",
      authorization_flow: "browser_oauth",
      status: "ready",
      configuration_pending: false,
      enabled: true,
      manual_cooldown: false,
      concurrency_limit: 2,
      proxy_mode: "default_gateway",
      proxy_profile_id: null,
      available_models: ["codex-model"],
      enabled_models: ["codex-model"],
      quota: { observed_at: null, plan_type: null, windows: [] },
      model_quotas: [],
      cooldown_until: null,
      automatic_cooldown: false,
      last_error: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    } as unknown as AccountPoolEnvironment;
    render(
      <AccountPoolCard
        environment={environment}
        onConfigure={vi.fn()}
        onEnabledChange={vi.fn()}
        onAuthorize={vi.fn()}
        onDelete={vi.fn()}
        onManageKey={vi.fn()}
        onManagePolicy={onManagePolicy}
      />,
    );

    fireEvent.doubleClick(screen.getByTestId("account-pool-card-env-double-click"));

    expect(onManagePolicy).toHaveBeenCalledWith(environment);
  });
});
