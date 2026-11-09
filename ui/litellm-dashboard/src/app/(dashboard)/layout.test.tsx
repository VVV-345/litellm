import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { use, useState } from "react";
import { AuthProvider } from "@/contexts/AuthContext";
import i18n from "@/i18n";
import Layout from "./layout";

const { pushMock, replaceMock } = vi.hoisted(() => ({ pushMock: vi.fn(), replaceMock: vi.fn() }));

let searchParamsValue = new URLSearchParams();
let pathnameValue = "/ui/api-keys";

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: pushMock, replace: replaceMock })),
  useSearchParams: vi.fn(() => searchParamsValue),
  usePathname: vi.fn(() => pathnameValue),
}));

vi.mock("@/components/DashboardHeader", () => ({
  DashboardHeader: () => <div data-testid="dashboard-header" />,
}));

vi.mock("@/app/(dashboard)/components/SidebarProvider", () => ({
  default: ({ sidebarCollapsed, onToggleCollapsed, setPage }: {
    sidebarCollapsed: boolean; onToggleCollapsed: () => void; setPage: (page: string) => void;
  }) => (
    <>
      <button data-testid="sidebar" data-collapsed={sidebarCollapsed} onClick={onToggleCollapsed}>
        Toggle sidebar
      </button>
      <button onClick={() => setPage("account-pool")}>Account Pool</button>
    </>
  ),
}));

vi.mock("@/components/DebugWarningBanner", () => ({
  DebugWarningBanner: () => null,
}));

vi.mock("@/components/NoRedisWarningBanner", () => ({
  NoRedisWarningBanner: () => null,
}));

vi.mock("@/components/LicenseExpiryBanner", () => ({
  LicenseExpiryBanner: () => null,
}));

vi.mock("@/components/UserBanner", () => ({
  UserBanner: () => null,
}));

vi.mock("@/contexts/ThemeContext", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/common_components/LoadingScreen", () => ({
  default: () => <div data-testid="loading-screen" />,
}));

type Deferred = { promise: Promise<void>; resolve: () => void };

const createDeferred = (): Deferred => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
};

let pendingUiConfig: Deferred;

vi.mock("@/components/networking", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/networking")>();
  return {
    ...actual,
    getUiConfig: vi.fn(() => pendingUiConfig.promise),
    setGlobalLitellmHeaderName: vi.fn(),
  };
});

describe("(dashboard) Layout", () => {
  afterEach(() => vi.restoreAllMocks());
  beforeEach(() => {
    vi.clearAllMocks();
    pushMock.mockReset();
    pendingUiConfig = createDeferred();
    searchParamsValue = new URLSearchParams();
    pathnameValue = "/ui/api-keys";
  });

  it("acknowledges the first click from logs and does not restart a pending account pool transition", async () => {
    pathnameValue = "/ui/logs/";
    const route = createDeferred();
    function AccountPoolDestination() {
      use(route.promise);
      return <div>Pool content</div>;
    }
    function TestNavigation() {
      const [opened, setOpened] = useState(false);
      pushMock.mockImplementation(() => setOpened(true));
      return <Layout>{opened ? <AccountPoolDestination /> : <div>Log content</div>}</Layout>;
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <TestNavigation />
        </AuthProvider>
      </QueryClientProvider>,
    );
    pendingUiConfig.resolve();
    const link = await screen.findByRole("button", { name: "Account Pool" });
    await act(async () => { fireEvent.click(link); });
    expect(screen.getByRole("status")).toHaveTextContent(i18n.t("nav.openingAccountPool"));
    expect(screen.getByText("Log content")).toBeVisible();
    await act(async () => { fireEvent.click(link); });
    expect(pushMock).toHaveBeenCalledExactlyOnceWith("/ui/account-pool/");
    await act(async () => route.resolve());
    expect(await screen.findByText("Pool content")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("collapses the sidebar on narrow screens and responds to breakpoint changes", async () => {
    let change: (() => void) | undefined;
    const media = {
      matches: true,
      addEventListener: vi.fn((_event: string, listener: () => void) => {
        change = listener;
      }),
      removeEventListener: vi.fn(),
    };
    vi.spyOn(window, "matchMedia").mockReturnValue(media as unknown as MediaQueryList);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <Layout>
            <div data-testid="page-content" />
          </Layout>
        </AuthProvider>
      </QueryClientProvider>,
    );
    pendingUiConfig.resolve();
    const sidebar = await screen.findByTestId("sidebar");
    await waitFor(() => expect(sidebar).toHaveAttribute("data-collapsed", "true"));
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 767px)");
    act(() => {
      media.matches = false;
      change?.();
    });
    expect(sidebar).toHaveAttribute("data-collapsed", "false");
    fireEvent.click(sidebar);
    expect(sidebar).toHaveAttribute("data-collapsed", "true");
  });

  it("does not mount route content until getUiConfig has resolved", async () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Layout>
            <div data-testid="page-content" />
          </Layout>
        </AuthProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId("loading-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("page-content")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-header")).not.toBeInTheDocument();

    pendingUiConfig.resolve();

    expect(await screen.findByTestId("page-content")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-header")).toBeInTheDocument();
    expect(screen.queryByTestId("loading-screen")).not.toBeInTheDocument();
  });

  it("keeps disabled guardrails hidden after getUiConfig resolves", async () => {
    const queryClient = new QueryClient();
    pathnameValue = "/ui/guardrails";
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Layout>
            <div data-testid="page-content" />
          </Layout>
        </AuthProvider>
      </QueryClientProvider>,
    );

    pendingUiConfig.resolve();

    expect(await screen.findByText("此模块未启用")).toBeInTheDocument();
    expect(screen.queryByTestId("page-content")).not.toBeInTheDocument();
  });

  it("redirects an invitation link to the onboarding route instead of rendering the dashboard shell", async () => {
    const queryClient = new QueryClient();
    searchParamsValue = new URLSearchParams("invitation_id=abc123");

    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Layout>
            <div data-testid="page-content" />
          </Layout>
        </AuthProvider>
      </QueryClientProvider>,
    );

    pendingUiConfig.resolve();

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(expect.stringContaining("/onboarding/?invitation_id=abc123")),
    );
    expect(screen.queryByTestId("page-content")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-header")).not.toBeInTheDocument();
    expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
  });
});
