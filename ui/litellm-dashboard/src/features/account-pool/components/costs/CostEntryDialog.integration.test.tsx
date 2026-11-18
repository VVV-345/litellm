/** 验证真实表单的账号归属、精确金额、失败重试和续费新增行为。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { createCost, type CostRecord } from "../../api/AccountPoolCostsApi";
import { CostEntryDialog } from "./CostEntryDialog";

vi.mock("../../api/AccountPoolCostsApi", () => ({ createCost: vi.fn() }));
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
    {children}
  </QueryClientProvider>
);
const account = { id: "6dd952e0-2f97-4749-a968-faf6626c8183", name: "已有账号" };
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("zh-CN");
});

it("retains input and the same id after a failed save, then closes on success", async () => {
  const close = vi.fn();
  vi.mocked(createCost).mockRejectedValueOnce(new Error("网络中断"));
  render(<CostEntryDialog accessToken="admin" target={{ category: "subscription", account }} onClose={close} />, {
    wrapper,
  });
  fireEvent.change(screen.getByLabelText("付款金额（人民币 ¥）"), { target: { value: "0.29" } });
  fireEvent.change(screen.getByLabelText("付款日期"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("服务开始日期"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("服务结束日期"), { target: { value: "2026-01-31" } });
  expect(createCost).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "保存费用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络中断");
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("0.29");
  const first = vi.mocked(createCost).mock.calls[0][1];
  const expectedEntry = {
    account_id: account.id,
    amount_cents: 29,
    currency: "CNY",
    paid_on: "2026-01-01",
    ends_on: "2026-01-31",
  };
  expect(first).toMatchObject(expectedEntry);
  expect(close).not.toHaveBeenCalled();
  vi.mocked(createCost).mockResolvedValueOnce({ ...first, created_at: "2026-01-01T00:00:00Z", created_by: "admin" });
  await userEvent.click(screen.getByRole("button", { name: "保存费用" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(createCost).toHaveBeenLastCalledWith("admin", first);
});

it("prefills a renewal but records a new period with a new id", async () => {
  const renewal: CostRecord = {
    id: "old",
    currency: "CNY",
    note: "",
    category: "subscription",
    account_id: account.id,
    name: "月订阅",
    amount_cents: 19900,
    paid_on: "2026-01-01",
    starts_on: "2026-01-01",
    ends_on: "2026-01-31",
    created_at: "2026-01-01T00:00:00Z",
    created_by: "admin",
  };
  const close = vi.fn();
  vi.mocked(createCost).mockResolvedValueOnce(renewal);
  render(
    <CostEntryDialog accessToken="admin" target={{ category: "subscription", account, renewal }} onClose={close} />,
    { wrapper },
  );
  expect(screen.getByLabelText("服务开始日期")).toHaveValue("2026-02-01");
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("199.00");
  fireEvent.change(screen.getByLabelText("服务结束日期"), { target: { value: "2026-02-28" } });
  await userEvent.click(screen.getByRole("button", { name: "保存费用" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  const expectedRenewal = {
    id: expect.not.stringMatching(/^old$/),
    account_id: account.id,
    starts_on: "2026-02-01",
    ends_on: "2026-02-28",
    amount_cents: 19900,
  };
  expect(createCost).toHaveBeenCalledWith("admin", expect.objectContaining(expectedRenewal));
});

it("applies the annual total only on request and saves the edited real payment for the existing account", async () => {
  const close = vi.fn();
  render(<CostEntryDialog accessToken="admin" target={{ category: "subscription", account }} onClose={close} />, {
    wrapper,
  });
  fireEvent.change(screen.getByLabelText("付款金额（人民币 ¥）"), { target: { value: "12.34" } });
  fireEvent.change(screen.getByLabelText("服务开始日期"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("服务结束日期"), { target: { value: "2026-12-31" } });
  fireEvent.change(screen.getByLabelText("备注（选填）"), { target: { value: "真实订单备注" } });
  fireEvent.change(screen.getByLabelText("Coding Plan 预设（选填）"), { target: { value: "kimi" } });
  fireEvent.change(screen.getByLabelText("套餐与计费周期"), { target: { value: "4" } });
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("12.34");
  await userEvent.click(screen.getByRole("button", { name: "套用名称和金额" }));
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("948.00");
  expect(screen.getByLabelText("套餐 / 费用名称")).toHaveValue("Kimi 会员（含 Code） Plus（年付）");
  expect(screen.getByLabelText("服务开始日期")).toHaveValue("2026-01-01");
  expect(screen.getByLabelText("服务结束日期")).toHaveValue("2026-12-31");
  expect(screen.getByLabelText("备注（选填）")).toHaveValue("真实订单备注");
  expect(createCost).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("付款金额（人民币 ¥）"), { target: { value: "900.50" } });
  await userEvent.click(screen.getByRole("button", { name: "保存费用" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(createCost).toHaveBeenCalledWith(
    "admin",
    expect.objectContaining({ account_id: account.id, amount_cents: 90050, currency: "CNY" }),
  );
});

it("clears the previous price when applying a plan without a verified quote and resets the tier on provider change", async () => {
  render(<CostEntryDialog accessToken="admin" target={{ category: "subscription", account }} onClose={vi.fn()} />, {
    wrapper,
  });
  fireEvent.change(screen.getByLabelText("付款金额（人民币 ¥）"), { target: { value: "118" } });
  fireEvent.change(screen.getByLabelText("Coding Plan 预设（选填）"), { target: { value: "volcengine" } });
  fireEvent.change(screen.getByLabelText("套餐与计费周期"), { target: { value: "0" } });
  expect(screen.getByRole("link", { name: "官方价格来源" })).toHaveAttribute(
    "href",
    "https://www.volcengine.com/activity/codingplan",
  );
  await userEvent.click(screen.getByRole("button", { name: "套用名称和金额" }));
  expect(screen.getByLabelText("付款金额（人民币 ¥）")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Coding Plan 预设（选填）"), { target: { value: "glm" } });
  expect(screen.getByLabelText("套餐与计费周期")).toHaveValue("");
  expect(screen.getByRole("button", { name: "套用名称和金额" })).toBeDisabled();
  expect(createCost).not.toHaveBeenCalled();
});

it("keeps coding presets out of independent server and proxy entries", () => {
  render(<CostEntryDialog accessToken="admin" target={{ category: "server" }} onClose={vi.fn()} />, { wrapper });
  expect(screen.queryByLabelText("Coding Plan 预设（选填）")).not.toBeInTheDocument();
});
