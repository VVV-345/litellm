/** 本文件验证创建授权弹窗与环境状态更新的交互，网络请求使用测试替身。 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolCreateDialog } from "./AccountPoolCreateDialog";
import { toast } from "@/lib/toast";
import type { AccountPoolAuthorization, AccountPoolEnvironment } from "./AccountPoolTypes";

const createMock = vi.fn();
const createDirectMock = vi.fn();
const createVertexMock = vi.fn();

vi.mock("./AccountPoolApi", () => ({
  createAccountPoolEnvironment: (...args: unknown[]) => createMock(...args),
  createDirectCredentialAccountPoolEnvironment: (...args: unknown[]) => createDirectMock(...args),
  createVertexAccountPoolEnvironment: (...args: unknown[]) => createVertexMock(...args),
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

const trackedAuthorization = {
  environment: { id: "env-3", channel: "cliproxyapi", supplier: "kimi", version: 2 },
  flow: "device_code",
  authorization_url: "https://auth.kimi.example/device",
  ssh_command: null,
  user_code: "EFGH-5678",
  expires_at: "2026-01-01T00:05:00Z",
};

const renderDialog = (props: Partial<Parameters<typeof AccountPoolCreateDialog>[0]> = {}) =>
  render(<AccountPoolCreateDialog accessToken="token-1" open onOpenChange={vi.fn()} onCreated={vi.fn()} {...props} />);

describe("AccountPoolCreateDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMock.mockReset();
    createDirectMock.mockReset();
    createVertexMock.mockReset();
    createMock.mockResolvedValue(browserAuthorization);
    createDirectMock.mockResolvedValue({ id: "gemini-1" });
    createVertexMock.mockResolvedValue({ id: "vertex-1" });
  });

  it.each(["ready", "cooling_down", "disabled"] as const)(
    "closes after the current authorization reaches %s",
    (status) => {
      const onOpenChange = vi.fn();
      const props = {
        accessToken: "token-1",
        open: true,
        onOpenChange,
        onCreated: vi.fn(),
        initialAuthorization: trackedAuthorization as AccountPoolAuthorization,
      };
      const { rerender } = render(<AccountPoolCreateDialog {...props} />);
      expect(onOpenChange).not.toHaveBeenCalled();

      rerender(
        <AccountPoolCreateDialog
          {...props}
          environments={[
            {
              ...trackedAuthorization.environment,
              version: 3,
              status,
              configuration_pending: false,
            } as AccountPoolEnvironment,
          ]}
        />,
      );

      expect(onOpenChange).toHaveBeenCalledWith(false);
      expect(toast.success).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    { id: "env-3", version: 1, status: "ready", configuration_pending: false },
    { id: "env-other", version: 3, status: "ready", configuration_pending: false },
    { id: "env-3", version: 3, status: "validating", configuration_pending: false },
    { id: "env-3", version: 3, status: "ready", configuration_pending: true },
  ] as const)("keeps authorization open for stale, unrelated or incomplete updates: %o", (environment) => {
    const onOpenChange = vi.fn();
    renderDialog({
      initialAuthorization: trackedAuthorization as AccountPoolAuthorization,
      environments: [environment as AccountPoolEnvironment],
      onOpenChange,
    });
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows the current authorization failure without closing the dialog", () => {
    const onOpenChange = vi.fn();
    renderDialog({
      initialAuthorization: trackedAuthorization as AccountPoolAuthorization,
      environments: [
        {
          ...trackedAuthorization.environment,
          version: 3,
          last_error: "credential save failed",
        } as AccountPoolEnvironment,
      ],
      onOpenChange,
    });
    expect(screen.getByRole("alert")).toHaveTextContent("credential save failed");
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("tracks completion for a newly created environment", async () => {
    createMock.mockResolvedValue(trackedAuthorization);
    const onOpenChange = vi.fn();
    const props = { accessToken: "token-1", open: true, onOpenChange, onCreated: vi.fn() };
    const { rerender } = render(<AccountPoolCreateDialog {...props} />);
    fireEvent.change(screen.getByLabelText(/环境名称|Environment name/i), { target: { value: "New account" } });
    fireEvent.click(screen.getByRole("button", { name: /新建|创建|Create/i }));
    expect(await screen.findByTestId("account-pool-authorization-panel")).toBeInTheDocument();

    rerender(
      <AccountPoolCreateDialog
        {...props}
        environments={[
          {
            ...trackedAuthorization.environment,
            version: 3,
            status: "ready",
            configuration_pending: false,
          } as AccountPoolEnvironment,
        ]}
      />,
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows the provider selected by the AI provider page", () => {
    renderDialog({ initialSupplier: "anthropic_claude" });
    expect(screen.getByText("Anthropic Claude · OAuth")).toBeInTheDocument();
    expect(screen.queryByTestId("account-pool-channel-select")).not.toBeInTheDocument();
  });

  it("sends the provider selected by the AI provider page to the create API", async () => {
    const user = userEvent.setup();
    renderDialog({ initialSupplier: "anthropic_claude" });

    await user.type(screen.getByLabelText(/环境名称|Environment name/i), "Claude account");
    await user.click(screen.getByRole("button", { name: /新建|创建|Create/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith(
        "token-1",
        expect.objectContaining({ channel: "cliproxyapi", supplier: "anthropic_claude" }),
      );
    });
  });

  it("creates a Gemini card with a direct API key", async () => {
    const onCreated = vi.fn();
    const onOpenChange = vi.fn();
    renderDialog({ initialSupplier: "gemini", onCreated, onOpenChange });

    fireEvent.change(screen.getByLabelText(/环境名称|Environment name/i), { target: { value: "Gemini account" } });
    fireEvent.change(screen.getByLabelText(/^API Key$|^API key$/i), { target: { value: "secret-key" } });
    fireEvent.change(screen.getByLabelText(/模型前缀|Model prefix/i), { target: { value: "team/" } });
    fireEvent.change(screen.getByLabelText(/自定义服务地址|Custom Base URL/i), {
      target: { value: "https://gemini.example/v1" },
    });
    fireEvent.change(screen.getByLabelText(/优先级|Priority/i), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/权重|Weight/i), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /新建|创建|Create/i }));

    await waitFor(() =>
      expect(createDirectMock).toHaveBeenCalledWith("token-1", {
        name: "Gemini account",
        supplier: "gemini",
        credential: {
          api_key: "secret-key",
          prefix: "team/",
          priority: 4,
          weight: 7,
          base_url: "https://gemini.example/v1",
          headers: [],
        },
      }),
    );
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("creates a Vertex card from a service account file", async () => {
    const onCreated = vi.fn();
    const onOpenChange = vi.fn();
    const file = new File(
      [JSON.stringify({ project_id: "demo", client_email: "svc@example.com", private_key: "secret" })],
      "service-account.json",
      { type: "application/json" },
    );
    renderDialog({ initialSupplier: "vertex", onCreated, onOpenChange });

    fireEvent.change(screen.getByLabelText(/环境名称|Environment name/i), { target: { value: "Vertex account" } });
    fireEvent.change(screen.getByLabelText(/服务账号 JSON|Service account JSON/i), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText(/区域|Location/i), { target: { value: "asia-east1" } });
    fireEvent.click(screen.getByRole("button", { name: /新建|创建|Create/i }));

    await waitFor(() => expect(createVertexMock).toHaveBeenCalledWith("token-1", "Vertex account", "asia-east1", file));
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
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
