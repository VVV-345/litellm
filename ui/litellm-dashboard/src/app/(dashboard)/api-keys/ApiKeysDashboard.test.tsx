import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

const { userDashboardSpy, setAccountId, session } = vi.hoisted(() => ({
  userDashboardSpy: vi.fn((_props: Record<string, unknown>) => null),
  setAccountId: vi.fn(),
  session: { role: "Admin", accountId: null as string | null },
}));

vi.mock("@/components/user_dashboard", () => ({
  default: (props: Record<string, unknown>) => userDashboardSpy(props),
}));

// AuthContext is still hydrating: userID has not been populated yet (the regression).
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    userID: null,
    userRole: "",
    userEmail: null,
    accessToken: null,
    premiumUser: false,
    setUserRole: vi.fn(),
    setUserEmail: vi.fn(),
  }),
}));

// useAuthorized decodes the cookie synchronously, so identity is already available.
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({
  default: () => ({
    isLoading: false,
    isAuthorized: true,
    token: "jwt",
    accessToken: "sk-access",
    userId: "u-123",
    userEmail: "admin@example.com",
    userRole: session.role,
    premiumUser: false,
    disabledPersonalKeyCreation: false,
    showSSOBanner: false,
  }),
}));

vi.mock("@/app/(dashboard)/hooks/teams/useTeams", () => ({
  teamListCall: vi.fn(() => new Promise(() => {})),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/app/(dashboard)/account-pool/useAccountPoolQuery", () => ({
  useAccountPoolQuery: () => ({ data: [{ id: "account-one", name: "测试账号" }], isError: false }),
}));
vi.mock("nuqs", () => ({ parseAsString: {}, useQueryState: () => [session.accountId, setAccountId] }));
vi.mock("./AccountKeyDialog", () => ({
  AccountKeyDialog: ({ cardId, onClose }: { cardId: string; onClose: () => void }) => (
    <button onClick={onClose}>关闭 {cardId}</button>
  ),
}));

import ApiKeysDashboard from "./ApiKeysDashboard";

describe("ApiKeysDashboard identity source", () => {
  it("passes the useAuthorized userID through even while AuthContext.userID is still null", () => {
    render(<ApiKeysDashboard />);

    expect(userDashboardSpy).toHaveBeenCalled();
    const props = userDashboardSpy.mock.calls[0][0];
    expect(props.userID).toBe("u-123");
  });
});

it("opens account-bound keys from the native keys page and preserves the standard dashboard", () => {
  session.role = "Admin";
  session.accountId = "account-one";
  render(<ApiKeysDashboard />);
  expect(screen.getByRole("combobox", { name: "账号专用密钥" })).toHaveValue("account-one");
  fireEvent.click(screen.getByRole("button", { name: "关闭 account-one" }));
  expect(setAccountId).toHaveBeenCalledWith(null);
  expect(userDashboardSpy).toHaveBeenCalled();
});

it("does not expose account key management to ordinary users even through a URL", () => {
  session.role = "Internal User";
  session.accountId = "account-one";
  render(<ApiKeysDashboard />);
  expect(screen.queryByRole("combobox", { name: "账号专用密钥" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "关闭 account-one" })).not.toBeInTheDocument();
});
