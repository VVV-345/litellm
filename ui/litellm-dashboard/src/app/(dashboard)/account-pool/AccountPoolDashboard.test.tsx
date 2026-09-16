import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolDashboard } from "./AccountPoolDashboard";
import type { AccountPoolQuotaRefreshStatus } from "./AccountPoolManagementApi";

const refreshStatus: AccountPoolQuotaRefreshStatus = {
  interval_minutes: 15,
  running: false,
  last_started_at: "2026-09-16T00:00:00Z",
  last_completed_at: "2026-09-16T00:05:00Z",
  next_refresh_at: "2026-09-16T00:20:00Z",
  last_failed_count: 0,
};

describe("AccountPoolDashboard", () => {
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
