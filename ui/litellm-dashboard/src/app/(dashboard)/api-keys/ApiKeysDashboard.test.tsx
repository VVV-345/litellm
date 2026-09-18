import { render } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

const { userDashboardSpy, session } = vi.hoisted(() => ({
  userDashboardSpy: vi.fn((_props: Record<string, unknown>) => null),
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
  useSearchParams: () => new URLSearchParams(session.accountId ? `account_id=${session.accountId}` : ""),
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

it("prefills the single native create-key form from a card link", () => {
  session.role = "Admin";
  session.accountId = "account-one";
  render(<ApiKeysDashboard />);
  const props = userDashboardSpy.mock.calls.at(-1)![0];
  expect(props.autoOpenCreate).toBe(true);
  expect(props.prefillData).toMatchObject({ account_id: "account-one" });
});

it("ignores card binding links for ordinary users", () => {
  session.role = "Internal User";
  session.accountId = "account-one";
  render(<ApiKeysDashboard />);
  const props = userDashboardSpy.mock.calls.at(-1)![0];
  expect(props.autoOpenCreate).toBe(false);
  expect(props.prefillData).toBeUndefined();
});
