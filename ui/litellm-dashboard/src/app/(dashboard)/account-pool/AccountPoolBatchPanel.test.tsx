/** 本文件验证批量面板把用户选择转换成带版本快照的后台任务请求。 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolBatchPanel } from "./AccountPoolBatchPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const listBatches = vi.fn();
const submitBatch = vi.fn();

vi.mock("./AccountPoolManagementApi", () => ({
  listAccountPoolBatches: (...args: unknown[]) => listBatches(...args),
  submitAccountPoolBatch: (...args: unknown[]) => submitBatch(...args),
}));

const environment = {
  id: "00000000-0000-4000-8000-000000000001",
  version: 7,
  name: "Primary account",
  provider: "openai",
  channel: "cliproxyapi",
  supplier: "openai_codex",
  status: "ready",
  configuration_pending: false,
  desired_configuration_version: 0,
  observed_configuration_version: 0,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: ["gpt-5"],
  enabled_models: ["gpt-5"],
  quota: { observed_at: null, plan_type: null, windows: [] },
  model_quotas: [],
  cooldown_until: null,
  automatic_cooldown: false,
  last_error: null,
  created_at: "2026-09-10T00:00:00Z",
  updated_at: "2026-09-10T00:00:00Z",
} as AccountPoolEnvironment;

const expectedBatchItem = {
  account_id: environment.id,
  version: 7,
  policy_version: 0,
};

const submittedBatch = {
  job_id: "00000000-0000-4000-8000-000000000002",
  action: "refresh",
  created_at: "2026-09-10T00:00:00Z",
  items: [],
};

const renderPanel = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccountPoolBatchPanel accessToken="token" environments={[environment]} />
    </QueryClientProvider>,
  );
};

describe("AccountPoolBatchPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listBatches.mockResolvedValue([]);
    submitBatch.mockResolvedValue(submittedBatch);
  });

  it("submits the selected account with its environment and policy versions", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByText("Primary account"));
    await user.click(screen.getByRole("button", { name: /执行 1 个账号|Run for 1 account/i }));

    await waitFor(() => expect(submitBatch).toHaveBeenCalledWith("token", "refresh", [expectedBatchItem]));
  });
});
