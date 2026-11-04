/** 验证确认操作、原兑换身份重用及失败不得显示恢复成功。 */
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import i18n from "@/i18n";
import { AccountPoolQuotaResetDialog } from "./AccountPoolQuotaResetDialog";

it.each(["uncertain", "no_credit", "nothing_to_reset"])("does not enable recovery for %s", async (status) => {
  vi.mocked(apiClient.post).mockResolvedValueOnce(operation).mockResolvedValueOnce({ ...operation, status });
  open();
  await screen.findByText("可用重置次数：2");
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认仅操作/ }));
  fireEvent.click(screen.getByRole("button", { name: "重置额度" }));
  await screen.findByText(/same-redemption/);
  expect(screen.getByRole("checkbox", { name: /^验证成功后恢复/ })).not.toBeChecked();
});

vi.mock("@/components/networking", () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));
const observation = { available: 2, eligible: true, credit_id: "credit-1", quota: { windows: [], balances: [] } };
const operation = {
  request_id: "same-redemption",
  environment_id: "first",
  status: "prepared",
  before: observation,
  windows_reset: 0,
  verification: "not_run",
};
const open = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountPoolQuotaResetDialog
        accessToken="admin"
        environment={{ id: "first", name: "7895", enabled_models: ["gpt-6-astra"] }}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("zh-CN");
  vi.mocked(apiClient.get).mockResolvedValue({ observation, operation: null });
});

it("requires confirmation and sends only the chosen card and prepared redemption identity", async () => {
  vi.mocked(apiClient.post)
    .mockResolvedValueOnce(operation)
    .mockResolvedValueOnce({ ...operation, status: "reset", windows_reset: 1 })
    .mockResolvedValueOnce({ passed: true, restored: true, reason: "restored" });
  open();
  expect(await screen.findByText("可用重置次数：2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重置额度" })).toBeDisabled();
  expect(apiClient.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认仅操作/ }));
  fireEvent.click(screen.getByRole("button", { name: "重置额度" }));
  expect(await screen.findByText(/已重置 1 个额度窗口/)).toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledWith(
    "/account_pool/environments/first/quota-reset/consume",
    expect.objectContaining({ body: { request_id: "same-redemption", confirmed: true } }),
  );
  expect(apiClient.post).toHaveBeenLastCalledWith(
    "/account_pool/environments/first/quota-reset/verify",
    expect.objectContaining({ body: { model: "gpt-6-astra", recover: true, confirmed: true } }),
  );
});

it("reconciles uncertain redemption without preparing a new identity", async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ observation, operation: { ...operation, status: "uncertain" } });
  vi.mocked(apiClient.post).mockResolvedValue({ ...operation, status: "already_redeemed" });
  open();
  const reconcile = await screen.findByRole("button", { name: "核对原次兑换" });
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认仅操作/ }));
  fireEvent.click(reconcile);
  expect(await screen.findByText(/该兑换已处理/)).toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledTimes(1);
});

it("shows authentication failure and does not claim recovery on HTTP 200", async () => {
  vi.mocked(apiClient.post).mockResolvedValue({ passed: false, restored: false, reason: "authentication_failed" });
  open();
  fireEvent.click(await screen.findByRole("button", { name: "验证调用" }));
  expect(await screen.findByText(/调用验证失败/)).toBeInTheDocument();
  expect(screen.queryByText("号池路由已恢复")).not.toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
    "/account_pool/environments/first/quota-reset/verify",
    expect.objectContaining({ body: { model: "gpt-6-astra", recover: false, confirmed: true } }),
  );
});

it("clears a previous successful verification when a new request fails", async () => {
  vi.mocked(apiClient.post)
    .mockResolvedValueOnce({ passed: true, restored: true, reason: "restored" })
    .mockRejectedValueOnce(new Error("fixture timeout"));
  open();
  fireEvent.click(await screen.findByRole("button", { name: "验证调用" }));
  expect(await screen.findByText(/号池状态已恢复/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "验证调用" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.queryByText(/号池状态已恢复/)).not.toBeInTheDocument();
});

it("reloads saved verification and hides it if a new verification fails", async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ observation, operation: null, last_verification: {
    model: "gpt-6-astra", source: "natural", observed_at: "2026-11-02T14:00:00Z",
    result: { passed: true, restored: true, reason: "restored" },
  } });
  vi.mocked(apiClient.post).mockRejectedValue(new Error("fixture timeout"));
  open();
  expect(await screen.findByText(/号池状态已恢复/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "验证调用" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.queryByText(/号池状态已恢复/)).not.toBeInTheDocument();
});

it("shows durable ordinary API proof with request identity instead of a management-only success", async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ observation, operation: null, last_verification: {
    model: "gpt-6-astra", source: "natural", observed_at: "2026-11-03T00:00:00Z",
    result: { passed: true, restored: true, reason: "restored" },
  }, last_api_call: {
    model: "gpt-6-astra", request_id: "ordinary-request", endpoint: "/v1/responses",
    verification_at: "2026-11-03T00:00:00Z", observed_at: "2026-11-03T00:01:00Z",
  } });
  open();
  expect(await screen.findByText(/普通 API 调用已验证/)).toHaveTextContent("ordinary-request");
  expect(screen.queryByText(/普通 API 调用仍需确认/)).not.toBeInTheDocument();
});


it("shows failed quota observation without losing the uncertain redemption identity", async () => {
  vi.mocked(apiClient.get).mockResolvedValue({ observation: null, operation: { ...operation, status: "uncertain" } });
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("额度查询失败");
  expect(screen.getByRole("button", { name: "核对原次兑换" })).toBeInTheDocument();
  expect(screen.getByText(/same-redemption/)).toBeInTheDocument();
  expect(screen.queryByText(/无适用次数/)).not.toBeInTheDocument();
  expect(apiClient.post).not.toHaveBeenCalled();
});
