import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";

const postMock = vi.fn();

vi.mock("@/components/networking", () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
  },
}));

const authorization = {
  flow: "browser_oauth" as const,
  authorization_url: "https://auth.example.com/oauth",
  ssh_command: "ssh -L 1455:localhost:8091 server",
  user_code: null,
  expires_at: "2026-10-08T00:05:00Z",
};

const renderPanel = () => {
  const view = (
    environmentId = "card-1",
    accessToken = "admin-token",
    authorizationUrl = authorization.authorization_url,
  ) => (
    <AccountPoolAuthorizationPanel
      authorization={{ ...authorization, authorization_url: authorizationUrl }}
      accessToken={accessToken}
      environmentId={environmentId}
      idPrefix="authorization"
    />
  );
  return { ...render(view()), view };
};

describe("AccountPoolAuthorizationPanel external browser", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
  });

  it("offers external authorization without the retired server browser", () => {
    renderPanel();

    expect(screen.queryByRole("button", { name: "Open server browser" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate local browser link" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Open authorization page" })).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(authorization.ssh_command)).not.toBeInTheDocument();
    expect(screen.queryByText(authorization.authorization_url)).not.toBeInTheDocument();
  });

  it("generates and copies a fresh external link with callback instructions", async () => {
    const freshUrl = "https://auth.example.com/oauth?state=fresh";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    postMock.mockReset().mockResolvedValue({ ...authorization, authorization_url: freshUrl });
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Generate local browser link" }));

    const link = await screen.findByRole("button", { name: "Open in this browser" });
    expect(link).toHaveAttribute("href", freshUrl);
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByDisplayValue(freshUrl)).toBeInTheDocument();
    expect(screen.getByText(authorization.ssh_command)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/account_pool/environments/card-1/authorize", {
      accessToken: "admin-token",
      headers: { "Idempotency-Key": expect.any(String) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Copy authorization link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(freshUrl));
    expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument();
  });

  it("shows external authorization failures and allows retry", async () => {
    postMock.mockReset().mockRejectedValue(new Error("Authorization unavailable"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Generate local browser link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Authorization unavailable");
    expect(screen.queryByRole("button", { name: "Open in this browser" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate local browser link" })).toBeEnabled();
  });

  it("discards the external link when switching account or user", async () => {
    postMock.mockReset().mockResolvedValue(authorization);
    const { rerender, view } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Generate local browser link" }));
    await screen.findByRole("button", { name: "Open in this browser" });

    rerender(view("card-2", "another-admin"));
    expect(screen.queryByRole("button", { name: "Open in this browser" })).not.toBeInTheDocument();
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
