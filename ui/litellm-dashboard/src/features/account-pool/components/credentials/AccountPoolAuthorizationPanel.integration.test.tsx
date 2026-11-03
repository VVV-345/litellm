/** 验证授权链接复制及手动回填，不启动本地或服务器浏览器。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";

const postMock = vi.fn();
const copyMock = vi.fn();
vi.mock("@/components/networking", () => ({ apiClient: { post: (...args: unknown[]) => postMock(...args) } }));
const authorization = {
  flow: "browser_oauth" as const,
  authorization_url:
    "https://auth.example/authorize?state=current&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback",
  ssh_command: null,
  user_code: null,
  expires_at: "2099-10-08T00:05:00Z",
};
const callback = "http://localhost:1455/auth/callback?state=current&code=fixture-code";
const renderPanel = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = (environmentId = "card-1", accessToken = "admin-token", url = authorization.authorization_url) => (
    <QueryClientProvider client={client}>
      <AccountPoolAuthorizationPanel
        authorization={{ ...authorization, authorization_url: url }}
        accessToken={accessToken}
        environmentId={environmentId}
        idPrefix="authorization"
      />
    </QueryClientProvider>
  );
  return { ...render(view()), view };
};

describe("AccountPoolAuthorizationPanel local OAuth", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
    copyMock.mockResolvedValue(undefined);
    postMock.mockResolvedValue({ status: "ready" });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copyMock } });
  });

  it("copies the exact link without opening a browser or starting a server session", async () => {
    const open = vi.spyOn(window, "open");
    renderPanel();
    expect(screen.getByLabelText("Authorization link")).toHaveValue(authorization.authorization_url);
    fireEvent.click(screen.getByRole("button", { name: "Copy authorization link" }));
    await waitFor(() => expect(copyMock).toHaveBeenCalledWith(authorization.authorization_url));
    expect(postMock).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Server OAuth browser")).not.toBeInTheDocument();
    open.mockRestore();
  });

  it("submits once and clears the sensitive callback after success", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("Callback URL"), { target: { value: callback } });
    const submit = screen.getByRole("button", { name: "Submit callback" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(await screen.findByRole("status")).toHaveTextContent("Callback accepted");
    expect(postMock).toHaveBeenCalledExactlyOnceWith("/account_pool/environments/card-1/oauth-callback", {
      accessToken: "admin-token",
      body: { state: "current", code: "fixture-code" },
    });
    expect(screen.getByLabelText("Callback URL")).toHaveValue("");
    expect(submit).toBeDisabled();
  });

  it("does not submit mismatched state or expose sensitive upstream errors", async () => {
    postMock.mockRejectedValue(new Error("failed fixture-code"));
    renderPanel();
    fireEvent.change(screen.getByLabelText("Callback URL"), {
      target: { value: callback.replace("state=current", "state=other") },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit callback" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not match");
    expect(postMock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Callback URL"), { target: { value: callback } });
    fireEvent.click(screen.getByRole("button", { name: "Submit callback" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not confirm"));
    expect(screen.getByRole("alert")).not.toHaveTextContent("fixture-code");
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it.each(["card", "token", "authorization"])("clears callback when %s changes", (change) => {
    const { rerender, view } = renderPanel();
    fireEvent.change(screen.getByLabelText("Callback URL"), { target: { value: callback } });
    rerender(
      view(
        change === "card" ? "card-2" : "card-1",
        change === "token" ? "new-token" : "admin-token",
        change === "authorization"
          ? authorization.authorization_url.replace("current", "new-state")
          : authorization.authorization_url,
      ),
    );
    expect(screen.getByLabelText("Callback URL")).toHaveValue("");
  });

  it("keeps device code and link copyable without a callback form", async () => {
    render(
      <AccountPoolAuthorizationPanel
        authorization={{ ...authorization, flow: "device_code", user_code: "ABCD" }}
        idPrefix="device"
      />,
    );
    expect(screen.getByDisplayValue("ABCD")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy authorization link" }));
    await waitFor(() => expect(copyMock).toHaveBeenCalledWith(authorization.authorization_url));
    expect(screen.queryByLabelText("Callback URL")).not.toBeInTheDocument();
  });
});
