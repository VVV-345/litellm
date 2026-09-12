/** 本文件验证批量面板把用户选择转换成带版本快照的后台任务请求。 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolBatchPanel } from "./AccountPoolBatchPanel";
import type { AccountPolicy, PolicyView } from "./AccountPoolManagementApi";
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
  authorization_flow: "browser_oauth",
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

const secondaryEnvironment = {
  ...environment,
  id: "00000000-0000-4000-8000-000000000003",
  version: 11,
  name: "Secondary account",
} as AccountPoolEnvironment;

const expectedPrimaryTarget = {
  account_id: environment.id,
  version: 7,
  policy_version: 3,
};

const expectedSecondaryTarget = {
  account_id: secondaryEnvironment.id,
  version: 11,
  policy_version: 5,
};

const policy: AccountPolicy = {
  tags: ["premium"],
  group: "codex",
  account_ids: [],
  routing: {
    strategy: "priority",
    priority: 10,
    weight: 2,
    is_backup: false,
    preferred_account_ids: [],
    session_affinity: false,
    session_affinity_ttl: 3600,
    quota_reserve_percent: 0,
    quota_snapshot_max_age: 300,
    token_budget_window_seconds: 3600,
    max_attempts: 1,
    retryable_statuses: [429, 502, 503, 504],
    backoff_ms: 1000,
    fallback_enabled: false,
  },
  excluded_models: [],
  model_aliases: [],
  transport: {
    image_generation: "inherit",
    websocket: "inherit",
    request_timeout_seconds: 120,
    debug_log_enabled: false,
  },
};

const policyView = {
  card_id: environment.id,
  version: 3,
  policy,
  runtime_status: "partial",
  capabilities: [],
  metadata_status: "saved",
} as PolicyView;

const secondaryPolicyView = {
  ...policyView,
  card_id: secondaryEnvironment.id,
  version: 5,
} as PolicyView;

const submittedBatch = {
  job_id: "00000000-0000-4000-8000-000000000002",
  action: "refresh",
  created_at: "2026-09-10T00:00:00Z",
  items: [],
};

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

const renderPanel = (client = createQueryClient()) =>
  render(
    <QueryClientProvider client={client}>
      <AccountPoolBatchPanel
        accessToken="token"
        environments={[environment, secondaryEnvironment]}
        policies={[policyView, secondaryPolicyView]}
      />
    </QueryClientProvider>,
  );

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

    await waitFor(() => expect(submitBatch).toHaveBeenCalledWith("token", "refresh", [expectedPrimaryTarget], null));
  });

  it("submits the selected policy template with every target version snapshot", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByText("Primary account"));
    await user.click(screen.getByText("Secondary account"));
    await user.click(screen.getByRole("combobox", { name: /批量动作|Bulk action/i }));
    await user.click(await screen.findByRole("option", { name: /更新策略|Update policy/i }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("combobox", { name: /策略模板|Policy template/i }));
    await user.click(await screen.findByRole("option", { name: /Primary account/ }));
    await user.click(screen.getByRole("button", { name: /执行 2 个账号|Run for 2 accounts/i }));

    await waitFor(() =>
      expect(submitBatch).toHaveBeenCalledWith(
        "token",
        "policy",
        [expectedPrimaryTarget, expectedSecondaryTarget],
        policy,
      ),
    );
  });

  it.each(["policy", "delete"] as const)("refreshes policy data after a completed %s batch", async (action) => {
    listBatches.mockResolvedValue([
      {
        ...submittedBatch,
        action,
        items: [
          {
            account_id: environment.id,
            status: "succeeded",
            attempts: 1,
            message: "Policy updated",
            finished_at: "2026-09-10T00:01:00Z",
          },
        ],
      },
    ]);
    const client = createQueryClient();
    const invalidateQueries = vi.spyOn(client, "invalidateQueries");

    renderPanel(client);

    await waitFor(() =>
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["account-pool", "policies", "token"] }),
    );
  });

  it("requires confirmation before submitting a delete batch", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByText("Primary account"));
    await user.click(screen.getByText("Secondary account"));
    await user.click(screen.getByRole("combobox", { name: /批量动作|Bulk action/i }));
    await user.click(await screen.findByRole("option", { name: /删除账号|Delete accounts/i }));
    await user.click(screen.getByRole("button", { name: /执行 2 个账号|Run for 2 accounts/i }));

    expect(submitBatch).not.toHaveBeenCalled();
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(/2 个账号|2 accounts/i);

    await user.click(screen.getByRole("button", { name: /确认删除|Delete selected accounts/i }));

    await waitFor(() =>
      expect(submitBatch).toHaveBeenCalledWith(
        "token",
        "delete",
        [expectedPrimaryTarget, expectedSecondaryTarget],
        null,
      ),
    );
  });

  it("reopens persisted authorization details from a completed batch", async () => {
    listBatches.mockResolvedValue([
      {
        ...submittedBatch,
        action: "authorize",
        items: [
          {
            account_id: environment.id,
            status: "succeeded",
            attempts: 1,
            message: "Authorization details generated",
            authorization: {
              flow: "device_code",
              authorization_url: "https://auth.example.com/device",
              ssh_command: null,
              user_code: "ABCD-1234",
              expires_at: "2026-09-10T00:05:00Z",
            },
            finished_at: "2026-09-10T00:00:01Z",
          },
        ],
      },
    ]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: /查看授权信息|View authorization/i }));

    expect(await screen.findByDisplayValue("ABCD-1234")).toBeInTheDocument();
    expect(screen.getByText("https://auth.example.com/device")).toBeInTheDocument();
  });
});
