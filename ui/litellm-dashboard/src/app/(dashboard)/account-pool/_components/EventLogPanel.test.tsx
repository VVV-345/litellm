// 本文件验证统一事件日志的筛选、原因说明和游标分页交互。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getEvents } from "../api";
import type { EventLogPage } from "../types";
import EventLogPanel from "./EventLogPanel";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, getEvents: vi.fn() };
});

const firstPage: EventLogPage = {
  status: "loaded",
  next_cursor: "next-page",
  events: [
    {
      event_id: "event-public-metadata",
      event_type: "public_metadata_task_retry_scheduled",
      occurred_at: "2026-08-21T10:05:00Z",
      channel_id: "channel-1",
      model_id: null,
      deployment_id: null,
      request_id: null,
      lease_id: null,
      reason_code: "source_transport",
      actor_type: "system",
      actor_id: "account_pool_public_metadata",
      outcome: "interrupted",
      safe_details: {
        kind: "public_metadata_task_retry_scheduled",
        task_id: "task-public-metadata",
        parser_run_id: "run-public-metadata",
        provider_id: "public_fixture",
        attempt_count: 1,
        next_attempt_at: "2026-08-21T10:06:00Z",
        failure_code: "source_transport",
      },
      audit: null,
      health: null,
      operational: {
        source: "public_metadata_task",
        operation_id: "task-public-metadata",
        outcome: "interrupted",
      },
    },
    {
      event_id: "event-1",
      event_type: "passive_health_result",
      occurred_at: "2026-08-21T10:00:00Z",
      channel_id: "channel-1",
      model_id: "gpt-5.6",
      deployment_id: "deployment-1",
      request_id: "request-1",
      lease_id: "lease-1",
      reason_code: "weekly_quota",
      actor_type: "system",
      actor_id: "account_pool_gateway",
      outcome: "failed",
      safe_details: {
        kind: "passive_health_result",
        outcome: "failed",
        transition: "cooldown",
        response_status_code: 429,
        latency_ms: 25,
      },
      audit: null,
      health: {
        account_id: "primary",
        source: "passive_request",
        outcome: "failed",
        transition: "cooldown",
        scope: "deployment",
        retry_at: null,
        probe_trigger: null,
      },
      operational: null,
    },
    {
      event_id: "event-3",
      event_type: "parser_task_interrupted",
      occurred_at: "2026-08-21T09:30:00Z",
      channel_id: "channel-1",
      model_id: null,
      deployment_id: null,
      request_id: "request-3",
      lease_id: null,
      reason_code: null,
      actor_type: "system",
      actor_id: "account_pool_parser_task",
      outcome: "interrupted",
      safe_details: {
        kind: "parser_task_interrupted",
        task_id: "task-3",
        parser_run_id: "run-3",
        provider_id: "openai_compatible",
        interruption_source: "stale_heartbeat",
      },
      audit: null,
      health: null,
      operational: {
        source: "parser_task",
        operation_id: "task-3",
        outcome: "interrupted",
      },
    },
    {
      event_id: "event-4",
      event_type: "parser_snapshot_exported",
      occurred_at: "2026-08-21T09:15:00Z",
      channel_id: "channel-1",
      model_id: null,
      deployment_id: null,
      request_id: null,
      lease_id: null,
      reason_code: null,
      actor_type: "system",
      actor_id: "account_pool_parser_snapshot",
      outcome: "succeeded",
      safe_details: {
        kind: "parser_snapshot_exported",
        parser_run_id: "run-4",
        attempt_count: 2,
        trigger: "retry",
      },
      audit: null,
      health: null,
      operational: {
        source: "parser_snapshot_export",
        operation_id: "run-4",
        outcome: "succeeded",
      },
    },
    {
      event_id: "event-5",
      event_type: "sync_retry_failed",
      occurred_at: "2026-08-21T09:10:00Z",
      channel_id: "channel-1",
      model_id: null,
      deployment_id: null,
      request_id: "reconcile:operation-5:2",
      lease_id: null,
      reason_code: "transport_failed",
      actor_type: "system",
      actor_id: "account_pool_reconciler",
      outcome: "failed",
      safe_details: {
        kind: "sync_retry_failed",
        operation_id: "operation-5",
        sync_action: "update_channel",
        attempt_count: 2,
        failure_code: "transport_failed",
      },
      audit: null,
      health: null,
      operational: {
        source: "sync_reconcile",
        operation_id: "operation-5",
        outcome: "failed",
      },
    },
    {
      event_id: "event-6",
      event_type: "request_settled",
      occurred_at: "2026-08-21T09:05:00Z",
      channel_id: "channel-1",
      model_id: "gpt-5.6",
      deployment_id: "deployment-1",
      request_id: "request-6",
      lease_id: "lease-6",
      reason_code: null,
      actor_type: "system",
      actor_id: "account_pool_state_store",
      outcome: "succeeded",
      safe_details: {
        kind: "request_settled",
        applied: true,
        success: true,
        status_code: 200,
        input_tokens: 12,
        output_tokens: 4,
        cost_usd: null,
        latency_ms: 25,
      },
      audit: null,
      health: null,
      operational: {
        source: "request_lifecycle",
        operation_id: "operation-6",
        outcome: "succeeded",
      },
    },
    {
      event_id: "event-7",
      event_type: "eligibility_restriction_activated",
      occurred_at: "2026-08-21T09:00:00Z",
      channel_id: "channel-1",
      model_id: "gpt-5.6",
      deployment_id: "deployment-1",
      request_id: null,
      lease_id: null,
      reason_code: "monthly_exhausted",
      actor_type: "system",
      actor_id: "account_pool_eligibility",
      outcome: "succeeded",
      safe_details: {
        kind: "eligibility_restriction_activated",
        restriction_id: "restriction-7",
        scope: "deployment",
        source: "restriction",
        state: "active",
        billing_route_id: null,
        starts_at: 1777000000,
        retry_at: null,
      },
      audit: null,
      health: null,
      operational: {
        source: "eligibility_transition",
        operation_id: "restriction-7",
        outcome: "succeeded",
      },
    },
  ],
};

const secondPage: EventLogPage = {
  status: "loaded",
  next_cursor: null,
  events: [
    {
      event_id: "event-2",
      event_type: "parser_override_set",
      occurred_at: "2026-08-21T09:00:00Z",
      channel_id: "channel-1",
      model_id: null,
      deployment_id: null,
      request_id: "request-2",
      lease_id: null,
      reason_code: null,
      actor_type: "user",
      actor_id: "admin-user",
      outcome: "succeeded",
      safe_details: {
        kind: "parser_override_set",
        outcome: { status: "succeeded" },
        override_id: "override-1",
        field_path: "/subscription/balance",
      },
      audit: {
        operation_id: null,
        actor_role: "proxy_admin",
        actor_action: "parser_override:set",
        actor_envelope_id: "envelope-1",
        outcome: "succeeded",
      },
      health: null,
      operational: null,
    },
  ],
};

const renderPanel = (initialChannelId: string | null = null) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <EventLogPanel accessToken="token" initialChannelId={initialChannelId} />
    </QueryClientProvider>,
  );
};

describe("EventLogPanel", () => {
  beforeEach(() => {
    vi.mocked(getEvents).mockImplementation(async (_token, filters) =>
      filters.cursor === "next-page" ? secondPage : firstPage,
    );
  });

  it("loads a small channel-filtered page and replaces it when navigating", async () => {
    const user = userEvent.setup();
    renderPanel("channel-1");

    expect(await screen.findByText("请求健康结果")).toBeInTheDocument();
    expect(screen.getByText("公开元数据等待重试")).toBeInTheDocument();
    expect(screen.getByText("解析任务已中断")).toBeInTheDocument();
    expect(screen.getByText("解析快照已导出")).toBeInTheDocument();
    expect(screen.getByText("后台同步失败")).toBeInTheDocument();
    expect(screen.getByText("请求已结算")).toBeInTheDocument();
    expect(screen.getByText("限制已生效")).toBeInTheDocument();
    expect(screen.getByText("周额度已耗尽 (weekly_quota)")).toBeInTheDocument();
    expect(getEvents).toHaveBeenCalledWith("token", { channel_id: "channel-1", limit: 10 });

    await user.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("设置人工修正")).toBeInTheDocument();
    expect(screen.queryByText("请求健康结果")).not.toBeInTheDocument();
    expect(getEvents).toHaveBeenLastCalledWith("token", {
      channel_id: "channel-1",
      cursor: "next-page",
      limit: 10,
    });

    await user.click(screen.getByRole("button", { name: "上一页" }));
    expect(await screen.findByText("请求健康结果")).toBeInTheDocument();

    const healthEventRow = screen.getByText("请求健康结果").closest("tr");
    expect(healthEventRow).not.toBeNull();
    await user.click(within(healthEventRow!).getByRole("button", { name: "查看事件详情" }));
    expect(screen.getByRole("dialog", { name: "事件详情" })).toBeInTheDocument();
    expect(screen.getByText("lease-1")).toBeInTheDocument();
    expect(screen.getByText(/"transition": "cooldown"/)).toBeInTheDocument();
  });

  it("applies typed text filters only after querying", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("请求健康结果");

    await user.type(screen.getByLabelText("模型"), "gpt-5.6");
    await user.type(screen.getByLabelText("Request ID"), "request-1");
    await user.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() =>
      expect(getEvents).toHaveBeenLastCalledWith("token", {
        model_id: "gpt-5.6",
        request_id: "request-1",
        limit: 10,
      }),
    );
  });
});
