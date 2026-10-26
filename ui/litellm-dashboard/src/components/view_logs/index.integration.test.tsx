import i18n from "@/i18n";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SpendLogsTable from "./index";
import { renderWithProviders, testQueryClient } from "../../../tests/test-utils";

const { useAuthorizedMock, useOrganizationsMock } = vi.hoisted(() => ({
  useAuthorizedMock: vi.fn(),
  useOrganizationsMock: vi.fn(),
}));

vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: useAuthorizedMock,
}));

vi.mock("@/app/(dashboard)/hooks/organizations/useOrganizations", () => ({
  useOrganizations: useOrganizationsMock,
}));

vi.mock("./RequestLogsPanel", () => ({
  default: function RequestLogsPanelMock() {
    return <div data-testid="request-logs-panel" />;
  },
}));

const fetchMock = vi.fn();

const jsonResponse = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: "OK",
  json: async () => body,
});

const requestedUrls = () => fetchMock.mock.calls.map(([url]) => String(url));

const emptyAuditLogs = { audit_logs: [], total: 0, page: 1, page_size: 50, total_pages: 0 };

const defaultProps = {
  accessToken: "sk-test",
  token: "jwt-test",
  userRole: "Admin",
  userID: "user-1",
  premiumUser: true,
};

const ORG_ADMIN_MEMBERSHIPS = [{ organization_id: "org-1", members: [{ user_id: "user-1", user_role: "org_admin" }] }];

const renderAs = (sessionRole: string, organizations: unknown[] = []) => {
  useAuthorizedMock.mockReturnValue({
    accessToken: "sk-test",
    userId: "user-1",
    userRole: sessionRole,
    premiumUser: true,
  });
  useOrganizationsMock.mockReturnValue({ data: organizations });
  return renderWithProviders(<SpendLogsTable {...defaultProps} userRole={sessionRole} />);
};

describe("SpendLogsTable network access by role", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    testQueryClient.clear();
    vi.clearAllMocks();
    useOrganizationsMock.mockReturnValue({ data: [] });
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes("/account_pool/cockpit/native/configuration")) return Response.json({ backend: "manager" });
      if (String(url).includes("/account_pool/environments")) return jsonResponse([]);
      if (String(url).includes("/audit")) {
        return jsonResponse(emptyAuditLogs);
      }
      if (String(url).includes("/v2/team/list")) {
        return jsonResponse({ teams: [] });
      }
      return jsonResponse({ keys: [], total_count: 0 });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("keeps Cockpit request logs usable without Manager tabs or account-list calls", async () => {
    fetchMock.mockImplementation(async (input: string | Request) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/account_pool/cockpit/native/configuration")) return Response.json({ backend: "cockpit" });
      return Response.json({ detail: "Legacy Manager API is disabled in Cockpit mode" }, { status: 503 });
    });
    useAuthorizedMock.mockReturnValue({ userId: "user-1", userRole: "Admin" });
    renderWithProviders(<SpendLogsTable {...defaultProps} />, { searchParams: { log_view: "operations" } });
    expect(await screen.findByText(/Cockpit.*请求日志/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Request Logs" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "运行日志" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "完整日志" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "日志设置" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("账号筛选")).not.toBeInTheDocument();
    expect(requestedUrls()).toHaveLength(1);
    expect(requestedUrls()[0]).toContain("/account_pool/cockpit/native/configuration");
  });

  it("shows backend discovery failure without falling back to Manager requests", async () => {
    fetchMock.mockImplementation(async () => Response.json({ detail: "offline" }, { status: 503 }));
    renderAs("Admin");
    expect(await screen.findByRole("alert")).toHaveTextContent("日志后端读取失败");
    expect(screen.getByTestId("request-logs-panel")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "运行日志" })).not.toBeInTheDocument();
    expect(requestedUrls()).toHaveLength(1);
    expect(requestedUrls()[0]).toContain("/account_pool/cockpit/native/configuration");
  });

  it("fires neither the audit nor the deleted-teams request for an internal user", async () => {
    const user = userEvent.setup();
    renderAs("Internal User");

    expect(requestedUrls().some((url) => url.includes("/key/list"))).toBe(false);
    await user.click(screen.getByRole("tab", { name: "Deleted Keys" }));
    await waitFor(() => expect(requestedUrls().some((url) => url.includes("/key/list"))).toBe(true), { timeout: 5000 });
    await user.click(screen.getByRole("tab", { name: "Request Logs" }));

    expect(requestedUrls().filter((url) => url.includes("/audit"))).toEqual([]);
    expect(requestedUrls().filter((url) => url.includes("/v2/team/list"))).toEqual([]);
  });

  it("fetches the deleted teams an org admin is entitled to, and still no audit logs", async () => {
    const user = userEvent.setup();
    renderAs("Internal User", ORG_ADMIN_MEMBERSHIPS);

    expect(requestedUrls().some((url) => url.includes("/v2/team/list"))).toBe(false);
    await user.click(screen.getByRole("tab", { name: "Deleted Teams" }));

    await waitFor(() =>
      expect(requestedUrls().some((url) => url.includes("/v2/team/list") && url.includes("status=deleted"))).toBe(true),
    );

    expect(requestedUrls().filter((url) => url.includes("/audit"))).toEqual([]);
  });

  it("fetches deleted teams and audit logs for an admin", async () => {
    const user = userEvent.setup();
    renderAs("Admin");

    expect(requestedUrls().some((url) => url.includes("/v2/team/list"))).toBe(false);
    await user.click(screen.getByRole("tab", { name: "Deleted Teams" }));
    await waitFor(() =>
      expect(requestedUrls().some((url) => url.includes("/v2/team/list") && url.includes("status=deleted"))).toBe(true),
    );

    expect(requestedUrls().filter((url) => url.includes("/audit"))).toEqual([]);

    await user.click(screen.getByRole("tab", { name: "Audit Logs" }));

    await waitFor(() => expect(requestedUrls().some((url) => url.includes("/audit"))).toBe(true));
  });

  it("leaves the audit request unsent when an admin selects a tab after Audit Logs", async () => {
    const user = userEvent.setup();
    renderAs("Admin");

    await user.click(screen.getByRole("tab", { name: "Deleted Teams" }));

    expect(screen.getByRole("tab", { name: "Deleted Teams" })).toHaveAttribute("aria-selected", "true");
    expect(requestedUrls().filter((url) => url.includes("/audit"))).toEqual([]);

    await user.click(screen.getByRole("tab", { name: "Audit Logs" }));

    await waitFor(() => expect(requestedUrls().some((url) => url.includes("/audit"))).toBe(true));
  });
});
