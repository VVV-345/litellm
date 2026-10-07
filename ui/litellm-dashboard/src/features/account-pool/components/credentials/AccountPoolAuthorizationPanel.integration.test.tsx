import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";

const postMock = vi.fn();
const getMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("@/components/networking", () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
    get: (...args: unknown[]) => getMock(...args),
    delete: (...args: unknown[]) => deleteMock(...args),
  },
}));

const authorization = {
  flow: "browser_oauth" as const,
  authorization_url: "https://auth.example.com/oauth",
  ssh_command: "ssh -L 1455:localhost:8091 server",
  user_code: null,
  expires_at: "2026-10-08T00:05:00Z",
};

const session = {
  id: "browser-1",
  environment_id: "card-1",
  status: "active",
  created_at: "2026-10-08T00:00:00Z",
  expires_at: "2026-10-08T00:05:00Z",
};

const renderPanel = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = (environmentId = "card-1", accessToken = "admin-token", authorizationUrl = authorization.authorization_url) => (
    <QueryClientProvider client={client}>
      <AccountPoolAuthorizationPanel
        authorization={{ ...authorization, authorization_url: authorizationUrl }}
        accessToken={accessToken}
        environmentId={environmentId}
        idPrefix="authorization"
      />
    </QueryClientProvider>
  );
  return { ...render(view()), view, client };
};

describe("AccountPoolAuthorizationPanel server browser", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
    postMock.mockResolvedValueOnce({ ...session, ticket: "single-use-ticket" }).mockResolvedValueOnce(undefined);
    getMock.mockResolvedValue(session);
    deleteMock.mockResolvedValue({ ...session, status: "cancelled" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("offers only the controlled server browser for browser OAuth", () => {
    renderPanel();

    expect(screen.getByRole("button", { name: "Open server browser" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Open authorization page" })).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(authorization.ssh_command)).not.toBeInTheDocument();
    expect(screen.queryByText(authorization.authorization_url)).not.toBeInTheDocument();
  });

  it("embeds the ticket-free noVNC URL and stops the session on cancellation", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));

    expect(await screen.findByTitle("Server OAuth browser")).toHaveAttribute(
      "src",
      "/account_pool/oauth-browser-sessions/browser-1/browser/vnc.html?autoconnect=true&resize=scale&path=account_pool%2Foauth-browser-sessions%2Fbrowser-1%2Fbrowser%2Fwebsockify",
    );
    expect(postMock).toHaveBeenNthCalledWith(2, "/account_pool/oauth-browser-sessions/browser-1/browser", {
      accessToken: "single-use-ticket",
    });
    fireEvent.click(screen.getByRole("button", { name: "Stop server browser" }));

    await waitFor(() => expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument());
    expect(deleteMock).toHaveBeenCalledWith("/account_pool/oauth-browser-sessions/browser-1", {
      accessToken: "admin-token",
    });
  });

  it("shows start failures and lets the user retry", async () => {
    postMock.mockReset().mockRejectedValue(new Error("Proxy is unavailable"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Proxy is unavailable");
    expect(screen.getByRole("button", { name: "Open server browser" })).toBeEnabled();
    expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument();
  });

  it("keeps the browser available and displays cancellation failures", async () => {
    deleteMock.mockRejectedValue(new Error("Could not stop worker"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));
    await screen.findByTitle("Server OAuth browser");
    fireEvent.click(screen.getByRole("button", { name: "Stop server browser" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not stop worker");
    expect(screen.getByTitle("Server OAuth browser")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop server browser" })).toBeEnabled();
  });

  it("shows status failures without losing the active browser", async () => {
    getMock.mockRejectedValue(new Error("Status service is unavailable"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Status service is unavailable");
    expect(screen.getByTitle("Server OAuth browser")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop server browser" })).toBeEnabled();
  });

  it.each(["completed", "expired", "cancelled", "failed"])(
    "removes the iframe after polling reaches %s",
    async (status) => {
      vi.useFakeTimers();
      getMock.mockResolvedValueOnce(session).mockResolvedValue({ ...session, status });
      renderPanel();
      fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));
      await act(() => vi.advanceTimersByTimeAsync(100));
      expect(screen.getByTitle("Server OAuth browser")).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent(new RegExp(status, "i"));
      const callsAfterCompletion = getMock.mock.calls.length;
      await act(() => vi.advanceTimersByTimeAsync(6000));
      expect(getMock).toHaveBeenCalledTimes(callsAfterCompletion);
    },
  );

  it("does not show the previous card's browser after switching account or user", async () => {
    const { rerender, view } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));
    await screen.findByTitle("Server OAuth browser");

    rerender(view("card-2", "another-admin"));
    expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open server browser" })).toBeEnabled();
  });

  it("keeps the active browser when the same card's refreshed OAuth URL changes", async () => {
    const { rerender, view } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Open server browser" }));
    const browser = await screen.findByTitle("Server OAuth browser");

    rerender(view("card-1", "admin-token", "https://auth.example.com/oauth?state=refreshed"));

    expect(screen.getByTitle("Server OAuth browser")).toBe(browser);
    expect(screen.getByRole("button", { name: "Stop server browser" })).toBeEnabled();
  });

  it("preserves the verification link and code for device authorization", () => {
    render(
      <AccountPoolAuthorizationPanel
        authorization={{ ...authorization, flow: "device_code", user_code: "ABCD-1234", ssh_command: null }}
        idPrefix="device"
      />,
    );

    expect(screen.getByDisplayValue("ABCD-1234")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open authorization page" })).toHaveAttribute(
      "href",
      "https://auth.example.com/oauth",
    );
    expect(screen.queryByRole("button", { name: "Open server browser" })).not.toBeInTheDocument();
  });
});
