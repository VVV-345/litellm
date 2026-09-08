/** 本文件验证创建授权弹窗与环境状态更新的交互，网络请求使用测试替身。 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCreateDialog } from "./AccountPoolCreateDialog";
import { toast } from "@/lib/toast";
import type { AccountPoolAuthorization, AccountPoolEnvironment } from "./AccountPoolTypes";

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

const linkOnlyAuthorization = {
  environment: { id: "env-3", channel: "freebuff2api", supplier: "freebuff", version: 2, status: "awaiting_authorization" },
  flow: "device_code",
  authorization_url: "https://www.codebuff.com/oauth/login?auth_code=one-time",
  ssh_command: null,
  user_code: null,
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
    vi.clearAllMocks();
    createMock.mockReset();
    createMock.mockResolvedValue(browserAuthorization);
  });

  it.each(["ready", "cooling_down", "disabled"] as const)("closes after the current authorization reaches %s", (status) => {
    const onOpenChange = vi.fn();
    const props = {
      accessToken: "token-1", open: true, onOpenChange, onCreated: vi.fn(),
      initialAuthorization: linkOnlyAuthorization as AccountPoolAuthorization,
    };
    const { rerender } = render(<AccountPoolCreateDialog {...props} />);
    expect(onOpenChange).not.toHaveBeenCalled();

    rerender(<AccountPoolCreateDialog {...props} environments={[{
      ...linkOnlyAuthorization.environment, version: 3, status, configuration_pending: false,
    } as AccountPoolEnvironment]} />);

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it.each([
    { id: "env-3", version: 1, status: "ready", configuration_pending: false },
    { id: "env-other", version: 3, status: "ready", configuration_pending: false },
    { id: "env-3", version: 3, status: "validating", configuration_pending: false },
    { id: "env-3", version: 3, status: "ready", configuration_pending: true },
  ] as const)("keeps authorization open for stale, unrelated or incomplete updates: %o", (environment) => {
    const onOpenChange = vi.fn();
    renderDialog({
      initialAuthorization: linkOnlyAuthorization as AccountPoolAuthorization,
      environments: [environment as AccountPoolEnvironment], onOpenChange,
    });
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows the current authorization failure without closing the dialog", () => {
    const onOpenChange = vi.fn();
    renderDialog({
      initialAuthorization: linkOnlyAuthorization as AccountPoolAuthorization,
      environments: [{
        ...linkOnlyAuthorization.environment, version: 3, last_error: "credential save failed",
      } as AccountPoolEnvironment], onOpenChange,
    });
    expect(screen.getByRole("alert")).toHaveTextContent("credential save failed");
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("tracks completion for a newly created environment", async () => {
    createMock.mockResolvedValue(linkOnlyAuthorization);
    const onOpenChange = vi.fn();
    const props = { accessToken: "token-1", open: true, onOpenChange, onCreated: vi.fn() };
    const { rerender } = render(<AccountPoolCreateDialog {...props} />);
    fireEvent.change(screen.getByLabelText(/环境名称|Environment name/i), { target: { value: "New account" } });
    fireEvent.click(screen.getByRole("button", { name: /创建|Create/i }));
    expect(await screen.findByTestId("account-pool-authorization-panel")).toBeInTheDocument();

    rerender(<AccountPoolCreateDialog {...props} environments={[{
      ...linkOnlyAuthorization.environment, version: 3, status: "ready", configuration_pending: false,
    } as AccountPoolEnvironment]} />);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("defaults to CLIProxyAPI and OpenAI Codex and lists all five suppliers", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId("account-pool-channel-select"));
    expect(screen.getByRole("option", { name: "CLIProxyAPI" })).toHaveAttribute("aria-selected", "true");
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

  it("sends freebuff2api with the freebuff supplier when selected", async () => {
    createMock.mockResolvedValue(linkOnlyAuthorization);
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText(/环境名称|Environment name/i), "FreeBuff account");
    await user.click(screen.getByTestId("account-pool-channel-select"));
    await user.click(screen.getByRole("option", { name: "FreeBuff2API" }));
    await user.click(screen.getByRole("button", { name: /创建|Create/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith(
        "token-1",
        expect.objectContaining({ channel: "freebuff2api", supplier: "freebuff" }),
      );
    });
  });

  it("shows only the freebuff supplier after switching to FreeBuff2API", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByTestId("account-pool-channel-select"));
    await user.click(await screen.findByRole("option", { name: "FreeBuff2API" }));
    await user.click(screen.getByTestId("account-pool-supplier-select"));
    expect(await screen.findByRole("option", { name: "FreeBuff (Codebuff)" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "OpenAI Codex" })).not.toBeInTheDocument();
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

  it("renders a link-only authorization panel for FreeBuff device-code results", async () => {
    renderDialog({ initialAuthorization: linkOnlyAuthorization as never });

    expect(screen.getByTestId("account-pool-authorization-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("account-pool-device-code")).not.toBeInTheDocument();
    expect(screen.queryByTestId("account-pool-browser-oauth")).not.toBeInTheDocument();
    expect(
      screen.getByText("https://www.codebuff.com/oauth/login?auth_code=one-time"),
    ).toBeInTheDocument();
  });
});
