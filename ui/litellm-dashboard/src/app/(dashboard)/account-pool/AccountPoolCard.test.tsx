import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolCard } from "./AccountPoolCard";
import type { AccountPoolEnvironment, AccountPoolProxyGateway } from "./AccountPoolTypes";

vi.mock("./AccountPoolApi", () => ({
  updateAccountPoolEnvironment: vi.fn(),
}));

const renderCard = (overrides: Partial<AccountPoolEnvironment> = {}, proxyGateway?: AccountPoolProxyGateway) => {
  const environment = {
    id: "env-claude-1",
    version: 1,
    name: "Claude account",
    provider: "openai",
    channel: "cliproxyapi",
    supplier: "anthropic_claude",
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
    renderCard(
      { channel, proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" },
      {
        port: 7891,
        profile_id: "clash-gateway-7891",
        name: "Clash 端口 7891",
        proxy_url: "http://host:7891",
        current_node: "美国01",
      },
    );

    expect(screen.getByText("Clash 端口 7891 · 美国01")).toBeInTheDocument();
  });

  it("keeps the selected profile visible while gateway details are unavailable", () => {
    renderCard({ proxy_mode: "profile", proxy_profile_id: "clash-gateway-7891" });
    expect(screen.getByText("clash-gateway-7891")).toBeInTheDocument();
  });

  it("shows translated channel and supplier labels instead of a static OpenAI label", () => {
    renderCard();

    expect(screen.getByText(/CLIProxyAPI · Anthropic Claude/)).toBeInTheDocument();
    expect(screen.queryByText("OpenAI Codex")).not.toBeInTheDocument();
  });

  it("shows the Kimi supplier label for a Kimi environment", () => {
    renderCard({ supplier: "kimi", enabled_models: ["kimi-model"], available_models: ["kimi-model"] });

    expect(screen.getByText(/CLIProxyAPI · Kimi/)).toBeInTheDocument();
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
});
