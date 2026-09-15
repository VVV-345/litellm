import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCredentialsPanel } from "./AccountPoolCredentialsPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const listCredentials = vi.fn();
const patchAuthFileStatus = vi.fn();

vi.mock("./AccountPoolManagementApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("./AccountPoolManagementApi")>();
  return {
    ...original,
    listAccountPoolCredentials: (...args: unknown[]) => listCredentials(...args),
    patchAccountPoolAuthFileStatus: (...args: unknown[]) => patchAuthFileStatus(...args),
  };
});

const environment = {
  id: "00000000-0000-4000-8000-000000000002",
  version: 4,
  name: "plus02",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  authorization_flow: "browser_oauth",
  status: "cooling_down",
  configuration_pending: false,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: [],
  enabled_models: [],
  quota: { observed_at: null, plan_type: "plus", windows: [], balances: [] },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: true,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} as AccountPoolEnvironment;

describe("AccountPoolCredentialsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listCredentials.mockResolvedValue([
      {
        id: `${environment.id}:default`,
        card_id: environment.id,
        card_name: environment.name,
        supplier: environment.supplier,
        kind: "oauth_file",
        status: "disabled",
        enabled: false,
        model_count: 0,
        auth_index: null,
      },
    ]);
    patchAuthFileStatus.mockResolvedValue(environment);
  });

  it("enables a disabled auth file even when its card remains enabled", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolCredentialsPanel accessToken="token" environments={[environment]} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: /启用|Enable/i }));

    await waitFor(() => expect(patchAuthFileStatus).toHaveBeenCalledWith("token", environment.id, false));
  });
});
