import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolDashboard } from "./AccountPoolDashboard";
import type { AccountPoolQuotaRefreshStatus } from "../../api/AccountPoolManagementApi";

const refreshStatus: AccountPoolQuotaRefreshStatus = {
  interval_minutes: 15,
  running: false,
  last_started_at: "2026-09-16T00:00:00Z",
  last_completed_at: "2026-09-16T00:05:00Z",
  next_refresh_at: "2026-09-16T00:20:00Z",
  last_failed_count: 0,
};

describe("AccountPoolDashboard", () => {
  it("reports failed statistics reads without hiding quota controls", () => {
    render(
      <AccountPoolDashboard
        environments={[]}
        statsByCard={new Map()}
        statsLoading={false}
        statsError
        renderCard={vi.fn()}
        quotaRefreshStatus={refreshStatus}
        onRefreshQuotas={vi.fn()}
        refreshingQuotas={false}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("调用统计读取失败");
    expect(screen.getAllByText(/暂无数据|Unknown/i).length).toBeGreaterThanOrEqual(5);
    expect(screen.getByRole("button", { name: /刷新额度|Refresh quotas/i })).toBeEnabled();
  });

  it("shows quota refresh timing and invokes the immediate refresh action", () => {
    const onRefreshQuotas = vi.fn();

    render(
      <AccountPoolDashboard
        environments={[]}
        statsByCard={new Map()}
        statsLoading={false}
        renderCard={vi.fn()}
        quotaRefreshStatus={refreshStatus}
        onRefreshQuotas={onRefreshQuotas}
        refreshingQuotas={false}
      />,
    );

    expect(screen.getByText(/最近额度刷新|Last quota refresh/i)).toBeInTheDocument();
    expect(screen.getByText(/下次额度刷新|Next quota refresh/i)).toBeInTheDocument();
    expect(screen.getByText("09/16 08:05")).toBeInTheDocument();
    expect(screen.getByText("09/16 08:20")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /刷新额度|Refresh quotas/i }));

    expect(onRefreshQuotas).toHaveBeenCalledOnce();
  });
});
