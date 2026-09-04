import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCreateDialog } from "./AccountPoolCreateDialog";

const createMock = vi.fn();

vi.mock("./AccountPoolApi", () => ({
  createAccountPoolEnvironment: (...args: unknown[]) => createMock(...args),
}));

vi.mock("@/lib/toast", () => ({
  toast: { error: vi.fn(), success: vi.fn(), fromError: vi.fn() },
}));

const browserAuthorization = {
  environment: { id: "env-1", channel: "cliproxyapi", supplier: "openai_codex" },
  flow: "browser_oauth",
  authorization_url: "https://auth.example.com/oauth",
  ssh_command: "ssh -N -L 1455:127.0.0.1:8091 user@example.com",
  user_code: null,
  expires_at: "2026-01-01T00:05:00Z",
};

const deviceAuthorization = {
  environment: { id: "env-2", channel: "cliproxyapi", supplier: "kimi" },
  flow: "device_code",
  authorization_url: "https://auth.kimi.example/device",
  ssh_command: null,
  user_code: "ABCD-1234",
  expires_at: "2026-01-01T00:05:00Z",
};

const renderDialog = (props: Partial<Parameters<typeof AccountPoolCreateDialog>[0]> = {}) =>
  render(
    <AccountPoolCreateDialog
      accessToken="token-1"
      open
      onOpenChange={vi.fn()}
      onCreated={vi.fn()}
      {...props}
    />,
  );

describe("AccountPoolCreateDialog", () => {
  beforeEach(() => {
    createMock.mockReset();
    createMock.mockResolvedValue(browserAuthorization);
  });

  it("defaults to CLIProxyAPI and OpenAI Codex and lists all five suppliers", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId("account-pool-channel-select"));
    expect(screen.getByRole("option", { name: "CLIProxyAPI" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: /FreeBuff2API/ })).toHaveAttribute("data-disabled");
    await user.keyboard("{Escape}");

    await user.click(screen.getByTestId("account-pool-supplier-select"));
    expect(screen.getByRole("option", { name: "OpenAI Codex" })).toHaveAttribute("aria-selected", "true");
    for (const supplier of ["OpenAI Codex", "Anthropic Claude", "Google Antigravity", "Kimi", "xAI"]) {
      expect(screen.getByRole("option", { name: supplier })).toBeInTheDocument();
    }
  });

  it("sends the selected channel and supplier to the create API", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/环境名称|Environment name/i), "Claude account");
    await user.click(screen.getByTestId("account-pool-supplier-select"));
    await user.click(screen.getByRole("option", { name: "Anthropic Claude" }));
    await user.click(screen.getByRole("button", { name: /创建|Create/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith(
        "token-1",
        expect.objectContaining({ channel: "cliproxyapi", supplier: "anthropic_claude" }),
      );
    });
  });

  it("shows FreeBuff2API as a disabled option and never calls the API for it", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId("account-pool-channel-select"));
    const freebuff = screen.getByRole("option", { name: /FreeBuff2API/ });
    expect(freebuff).toHaveAttribute("data-disabled");

    await user.click(screen.getByRole("button", { name: /创建|Create/i }).closest("form") ?? screen.getByRole("button", { name: /创建|Create/i }));
    expect(createMock).not.toHaveBeenCalled();
  });

  it("renders the SSH command for browser OAuth results and no device-code field", async () => {
    renderDialog({ initialAuthorization: browserAuthorization as never });

    expect(screen.getByTestId("account-pool-browser-oauth")).toBeInTheDocument();
    expect(screen.getByDisplayValue(browserAuthorization.ssh_command)).toBeInTheDocument();
    expect(screen.queryByTestId("account-pool-device-code")).not.toBeInTheDocument();
  });

  it("renders a copyable user code for device-code results and no SSH field", async () => {
    renderDialog({ initialAuthorization: deviceAuthorization as never });

    expect(screen.getByTestId("account-pool-device-code")).toBeInTheDocument();
    expect(screen.getByDisplayValue(deviceAuthorization.user_code)).toBeInTheDocument();
    expect(screen.queryByTestId("account-pool-browser-oauth")).not.toBeInTheDocument();
  });
});
