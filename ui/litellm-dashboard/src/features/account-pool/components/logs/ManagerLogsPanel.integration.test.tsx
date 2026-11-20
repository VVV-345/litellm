/** 验证浏览器事件筛选、删除确认和脱敏错误提示对用户可见。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ManagerLogsPanel from "./ManagerLogsPanel";

const session = "33333333-3333-4333-8333-333333333333";
const card = "11111111-1111-4111-8111-111111111111";
const paramsOf = (target: RequestInfo | URL) => new URL(String(target), "http://fixture.test").searchParams;

function renderLogs() {
  const client = new QueryClient();
  return render(<QueryClientProvider client={client}><ManagerLogsPanel accessToken="fixture-admin" /></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

it("shows a safe waiting reason, sends both filters and only deletes after confirmation", async () => {
  const requests: Array<{ params: URLSearchParams; method?: string }> = [];
  const fetchFixture: typeof fetch = async (target, init) => {
    const params = paramsOf(target);
    requests.push({ params, method: init?.method });
    if (init?.method === "DELETE") return Response.json({ deleted: 2 });
    return Response.json({ has_more: false, items: [{
      event_id: "44444444-4444-4444-8444-444444444444", occurred_at: "2026-11-18T01:02:03Z",
      card_id: card, session_id: session, severity: "warning", step: "password", state: "waiting",
      reason: "browser_security_challenge", http_status: 403, duration_ms: null,
    }] });
  };
  vi.stubGlobal("fetch", fetchFixture);
  renderLogs();
  expect(await screen.findByText(/检测到浏览器安全验证/)).toHaveTextContent("HTTP 403");
  expect(screen.getByText(/输入密码/)).toHaveTextContent("等待人工");
  fireEvent.change(screen.getByLabelText("会话 ID"), { target: { value: session } });
  fireEvent.change(screen.getByLabelText("卡片 ID"), { target: { value: card } });
  fireEvent.click(screen.getByRole("button", { name: "筛选" }));
  await waitFor(() => expect(requests.some(({ params }) => (
    params.get("session_id") === session && params.get("card_id") === card
  ))).toBe(true));

  const days = screen.getByRole("spinbutton", { name: "删除多少天前" });
  fireEvent.change(days, { target: { value: "0" } });
  const cleanup = screen.getByRole("button", { name: "清理旧日志" });
  expect(cleanup).toBeDisabled();
  fireEvent.change(days, { target: { value: "14" } });
  fireEvent.click(cleanup);
  const dialog = await screen.findByRole("alertdialog", { name: "删除 14 天前的 Manager 日志？" });
  expect(requests.some(({ method }) => method === "DELETE")).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect(await screen.findByText("已删除 2 条 Manager 日志。")).toBeInTheDocument();
  expect(requests.find(({ method }) => method === "DELETE")?.params.toString()).toBe("older_than_days=14");
});

it("shows a retry message without showing the upstream error body", async () => {
  vi.stubGlobal("fetch", async () => Response.json({ detail: "fixture-private-error" }, { status: 502 }));
  renderLogs();
  expect(await screen.findByRole("alert")).toHaveTextContent("无法加载 Manager 日志，请刷新后重试。");
  expect(screen.queryByText("fixture-private-error")).not.toBeInTheDocument();
});


it.each([
  ["form_fill_timeout", "填写表单超时"], ["form_fill_failed", "填写表单失败"],
  ["form_submit_timeout", "提交表单超时"], ["form_submit_failed", "提交表单失败"],
])("displays %s with the actual form stage", async (reason, label) => {
  vi.stubGlobal("fetch", async () => Response.json({ has_more: false, items: [{
    event_id: "44444444-4444-4444-8444-444444444444", occurred_at: "2026-11-20T01:02:03Z",
    card_id: card, session_id: session, severity: "warning", step: "password", state: "waiting",
    reason, http_status: null, duration_ms: null,
  }] }));
  renderLogs();
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(screen.getByText(/输入密码/)).toHaveTextContent("等待人工");
  expect(screen.queryByText(reason)).not.toBeInTheDocument();
});
