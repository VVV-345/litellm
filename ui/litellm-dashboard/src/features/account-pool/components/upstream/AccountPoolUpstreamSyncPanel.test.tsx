/** 本文件验证上游更新面板只允许检测新版本，并在通过复测前阻止正式合并。 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolUpstreamSyncPanel } from "./AccountPoolUpstreamSyncPanel";
import type { UpstreamSyncView } from "../../api/AccountPoolManagementApi";
import { ApiError } from "@/lib/http/client";

const getStatus = vi.fn();
const analyze = vi.fn();
const promote = vi.fn();
const getReview = vi.fn();
const getLiteLLMStatus = vi.fn();
const analysisDispatch = {
  request_id: "c24d4fcb-ff4a-424e-b86e-e3fe7a9ce649",
  action: "analyze",
  target_tag: "v7.3.2",
  state: "queued",
};
const promotionDispatch = {
  request_id: "6f0c14a4-78c4-4cc8-a2f8-8fac2e177c24",
  action: "promote",
  target_tag: "v7.3.2",
  state: "queued",
};
const reviewPackage = {
  filename: "codex-upstream-review-v7.3.2.md",
  branch: "codex/upstream-sync",
  target_tag: "v7.3.2",
  content: "# Review\n",
};

vi.mock("../../api/AccountPoolManagementApi", () => ({
  getAccountPoolUpstreamSync: (...args: unknown[]) => getStatus(...args),
  analyzeAccountPoolUpstream: (...args: unknown[]) => analyze(...args),
  promoteAccountPoolUpstream: (...args: unknown[]) => promote(...args),
  getAccountPoolCodexReview: (...args: unknown[]) => getReview(...args),
  getLiteLLMUpstreamSync: (...args: unknown[]) => getLiteLLMStatus(...args),
  analyzeLiteLLMUpstream: vi.fn(),
  promoteLiteLLMUpstream: vi.fn(),
  getLiteLLMCodexReview: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    fromError: vi.fn(),
  },
}));

const status = (state: UpstreamSyncView["report"]["state"] = "idle"): UpstreamSyncView => ({
  target: "cliproxyapi",
  upstream_repository: "router-for-me/CLIProxyAPI",
  fork_repository: "VVV-345/CLIProxyAPI",
  sync_branch: "codex/upstream-sync",
  current_tag: "v7.2.146",
  latest_tag: "v7.3.2",
  latest_release_url: "https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.3.2",
  update_available: true,
  dispatch_configured: true,
  report: {
    schema_version: 1,
    state,
    action: state === "passed" ? "analyze" : "none",
    request_id: state === "passed" ? "af094d6b-f0da-4ad6-aa59-7704393a81a4" : null,
    target_tag: state === "passed" ? "v7.3.2" : null,
    base_sha: state === "passed" ? "e851070a0d08fb631d5ee7c64ecfab9a30ecbd2a" : null,
    candidate_sha: state === "passed" ? "92589ae0e0592e5469fb5f2e7859ab9664155419" : null,
    conflict_files: [],
    failed_steps: [],
    message: "",
    workflow_url: null,
    updated_at: state === "passed" ? "2026-09-14T12:00:00Z" : null,
  },
});

const renderPanel = (target: "cliproxyapi" | "litellm" = "cliproxyapi") =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountPoolUpstreamSyncPanel accessToken="token" target={target} />
    </QueryClientProvider>,
  );

describe("AccountPoolUpstreamSyncPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getStatus.mockResolvedValue(status());
    const litellmStatus: UpstreamSyncView = {
      ...status(),
      target: "litellm",
      current_tag: "v1.100.0",
      latest_tag: "v1.101.0",
    };
    getLiteLLMStatus.mockResolvedValue(litellmStatus);
    analyze.mockResolvedValue(analysisDispatch);
    promote.mockResolvedValue(promotionDispatch);
    getReview.mockResolvedValue(reviewPackage);
  });

  it("shows the LiteLLM version without requiring the CLIProxyAPI query", async () => {
    getStatus.mockRejectedValue(new Error("CLIProxyAPI unavailable"));
    renderPanel("litellm");
    expect(await screen.findByText("v1.101.0")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("identifies the failing target and recovers on retry", async () => {
    getLiteLLMStatus.mockRejectedValueOnce(new ApiError("Not Found", 404, {}));
    renderPanel("litellm");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("LiteLLM");
    expect(alert).not.toHaveTextContent("CLIProxyAPI");
    await userEvent.click(screen.getByRole("button", { name: /重试|Retry/i }));
    expect(await screen.findByText("v1.101.0")).toBeInTheDocument();
  });

  it("explains GitHub rate limiting without rendering raw server errors or offering promotion", async () => {
    getLiteLLMStatus.mockRejectedValue(new ApiError("sensitive server detail", 429, {}));
    renderPanel("litellm");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/GitHub/);
    expect(alert).toHaveTextContent(/限流|rate limit/i);
    expect(alert).toHaveTextContent(/Token|token/);
    expect(alert).not.toHaveTextContent("sensitive server detail");
    expect(screen.queryByRole("button", { name: /正式合并更新|Merge validated update/i })).not.toBeInTheDocument();
  });

  it("starts an isolated compatibility analysis for a newer release", async () => {
    const user = userEvent.setup();
    renderPanel();

    const analyzeButton = await screen.findByRole("button", { name: /检测合并效果|Test merge compatibility/i });
    expect(screen.getByText("v7.2.146")).toBeInTheDocument();
    expect(screen.getByText("v7.3.2")).toBeInTheDocument();
    await user.click(analyzeButton);

    await waitFor(() => expect(analyze).toHaveBeenCalledWith("token"));
    expect(screen.getByRole("button", { name: /正式合并更新|Merge validated update/i })).toBeDisabled();
  });

  it("requires confirmation before promoting a passed compatibility report", async () => {
    const user = userEvent.setup();
    getStatus.mockResolvedValue(status("passed"));
    renderPanel();

    await user.click(await screen.findByRole("button", { name: /正式合并更新|Merge validated update/i }));
    await user.click(screen.getByRole("button", { name: /确认合并并构建|Merge and build/i }));

    await waitFor(() => expect(promote).toHaveBeenCalledWith("token"));
  });
});
