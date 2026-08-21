// 本文件验证统一事件日志的筛选、原因说明和游标分页交互。

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
    },
  ],
};

const secondPage: EventLogPage = {
  status: "loaded",
  next_cursor: null,
  events: [
    {
      event_id: "event-2",
      event_type: "channel_update",
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
      safe_details: { kind: "channel_update", outcome: { status: "succeeded" } },
      audit: {
        operation_id: null,
        actor_role: "proxy_admin",
        actor_action: "channel:update",
        actor_envelope_id: "envelope-1",
        outcome: "succeeded",
      },
      health: null,
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

  it("loads a channel-filtered page and appends the next cursor page", async () => {
    const user = userEvent.setup();
    renderPanel("channel-1");

    expect(await screen.findByText("请求健康结果")).toBeInTheDocument();
    expect(screen.getByText("周额度已耗尽 (weekly_quota)")).toBeInTheDocument();
    expect(getEvents).toHaveBeenCalledWith("token", { channel_id: "channel-1", limit: 50 });

    await user.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByText("修改渠道")).toBeInTheDocument();
    expect(getEvents).toHaveBeenLastCalledWith("token", {
      channel_id: "channel-1",
      cursor: "next-page",
      limit: 50,
    });

    await user.click(screen.getAllByRole("button", { name: "查看事件详情" })[0]);
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
        limit: 50,
      }),
    );
  });
});
