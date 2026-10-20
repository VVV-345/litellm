import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { AccountPoolFeedbackPanel } from "./AccountPoolFeedbackPanel";
import { FeedbackIssueDialog } from "./FeedbackIssueDialog";

const api = vi.hoisted(() => ({
  listFeedbackIssues: vi.fn(), getFeedbackIssue: vi.fn(), createFeedbackIssue: vi.fn(), editFeedbackIssue: vi.fn(),
  deleteFeedbackIssue: vi.fn(), mutateFeedbackIssue: vi.fn(), listFeedbackTokens: vi.fn(),
  createFeedbackToken: vi.fn(), revokeFeedbackToken: vi.fn(), uploadFeedbackAttachment: vi.fn(),
  downloadFeedbackAttachment: vi.fn(),
}));
vi.mock("../../api/AccountPoolFeedbackApi", () => api);

const issue = {
  id: "issue-1", revision: 3, title: "列表未刷新", description: "保存后列表不更新", steps: "保存设置", module: "号池",
  page_path: "/ui/account-pool", discovered_version: "v1.2.3", priority: "normal", status: "pending",
  created_at: "2026-10-20T08:00:00Z", updated_at: "2026-10-20T08:00:00Z", assigned_to: null,
  resolution: "", commit: "", verification: "", deployed_version: "", events: [], attachments: [],
};
const stats = { pending: 1, in_progress: 0, pending_verification: 0, resolved: 2 };
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}>
    {children}
  </QueryClientProvider>
);

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
  vi.resetAllMocks();
  api.listFeedbackIssues.mockResolvedValue({ items: [issue], total: 1, stats });
  api.getFeedbackIssue.mockResolvedValue(issue);
  api.listFeedbackTokens.mockResolvedValue([]);
});

describe("feedback creation", () => {
  it("submits the explicit form and retains input when saving fails", async () => {
    api.createFeedbackIssue.mockRejectedValueOnce(new Error("保存失败"));
    const saved = vi.fn();
    render(<FeedbackIssueDialog accessToken="admin" pagePath="/ui/account-pool" onClose={vi.fn()} onSaved={saved} />, { wrapper });
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: " 新问题 " } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: " 详情 " } });
    expect(api.createFeedbackIssue).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "保存反馈" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
    expect(screen.getByLabelText("标题")).toHaveValue(" 新问题 ");
    expect(saved).not.toHaveBeenCalled();
    api.createFeedbackIssue.mockResolvedValueOnce({ ...issue, title: "新问题" });
    await userEvent.click(screen.getByRole("button", { name: "保存反馈" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(api.createFeedbackIssue).toHaveBeenLastCalledWith("admin", expect.objectContaining({ title: "新问题", description: "详情", page_path: "/ui/account-pool", priority: "normal" }));
  });

  it("uploads selected attachments after saving and shows attachment metadata in detail", async () => {
    const attachment = { id: "attachment-1", filename: "capture.png", content_type: "image/png", size: 3 };
    api.createFeedbackIssue.mockResolvedValueOnce({ ...issue, revision: 1, attachments: [] });
    api.uploadFeedbackAttachment.mockResolvedValueOnce({ ...issue, revision: 2, attachments: [attachment] });
    const saved = vi.fn();
    const view = render(<FeedbackIssueDialog accessToken="admin" onClose={vi.fn()} onSaved={saved} />, { wrapper });
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "新问题" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "详情" } });
    const file = new File(["png"], "capture.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("附件"), file);
    await userEvent.click(screen.getByRole("button", { name: "保存反馈" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(api.uploadFeedbackAttachment).toHaveBeenCalledWith("admin", "issue-1", 1, file);
    view.unmount();
    api.listFeedbackIssues.mockResolvedValueOnce({
      items: [{ ...issue, revision: 2, attachments: [attachment] }], total: 1, stats,
    });
    api.getFeedbackIssue.mockResolvedValueOnce({ ...issue, revision: 2, attachments: [attachment] });
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(await screen.findByRole("button", { name: "列表未刷新" }));
    expect(await screen.findByText("capture.png (3 B)")).toBeInTheDocument();
  });
});

describe("feedback management", () => {
  it("refreshes detail from the server before acting", async () => {
    api.getFeedbackIssue.mockResolvedValue({ ...issue, revision: 4, status: "in_progress", description: "更新后的详情" });
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(await screen.findByRole("button", { name: "列表未刷新" }));
    expect(await within(screen.getByRole("dialog")).findByText("更新后的详情")).toBeInTheDocument();
    expect(api.getFeedbackIssue).toHaveBeenCalledWith("admin", "issue-1");
    expect(within(screen.getByRole("dialog")).getByText("修订版本 4")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "提交修复" })).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "认领" })).not.toBeInTheDocument();
  });

  it("shows server counts and refreshes the edited record with its revision", async () => {
    api.editFeedbackIssue.mockResolvedValue({ ...issue, title: "已修改", revision: 4 });
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    expect(await screen.findByRole("button", { name: "列表未刷新" })).toBeInTheDocument();
    expect(screen.getByText("待处理：1")).toBeInTheDocument();
    expect(screen.getByText("已解决：2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "编辑 列表未刷新" }));
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "已修改" } });
    api.listFeedbackIssues.mockResolvedValue({ items: [{ ...issue, title: "已修改", revision: 4 }], total: 1, stats });
    await userEvent.click(screen.getByRole("button", { name: "保存反馈" }));
    expect(await screen.findByRole("button", { name: "已修改" })).toBeInTheDocument();
    expect(api.editFeedbackIssue).toHaveBeenCalledWith("admin", "issue-1", expect.objectContaining({ expected_revision: 3, title: "已修改" }));
  });

  it("keeps a conflicting deletion visible and removes it only after success", async () => {
    api.deleteFeedbackIssue.mockRejectedValueOnce(new Error("反馈已更新，请刷新后重试"));
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(await screen.findByRole("button", { name: "删除 列表未刷新" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请刷新后重试");
    expect(screen.getByRole("dialog", { name: "删除反馈" })).toBeInTheDocument();
    api.deleteFeedbackIssue.mockResolvedValueOnce(undefined);
    api.listFeedbackIssues.mockResolvedValue({ items: [], total: 0, stats: { ...stats, pending: 0 } });
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("暂无反馈")).toBeInTheDocument();
    expect(api.deleteFeedbackIssue).toHaveBeenLastCalledWith("admin", "issue-1", 3);
  });

  it("loads the next server page and resets pagination when searching", async () => {
    api.listFeedbackIssues.mockResolvedValue({ items: [issue], total: 51, stats });
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await screen.findByRole("button", { name: "列表未刷新" });
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(api.listFeedbackIssues).toHaveBeenLastCalledWith("admin", { status: "unresolved", q: "", limit: 50, offset: 50 }));
    fireEvent.change(screen.getByLabelText("搜索反馈"), { target: { value: "刷新" } });
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    await waitFor(() => expect(api.listFeedbackIssues).toHaveBeenLastCalledWith("admin", { status: "unresolved", q: "刷新", limit: 50, offset: 0 }));
  });

  it("retains unresolved state after a failed action and shows evidence after success", async () => {
    api.mutateFeedbackIssue.mockRejectedValueOnce(new Error("认领失败"));
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(await screen.findByRole("button", { name: "列表未刷新" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "认领" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "执行认领" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("认领失败");
    expect(within(screen.getByRole("dialog")).getByText("待处理")).toBeInTheDocument();
    api.mutateFeedbackIssue.mockResolvedValueOnce({ ...issue, status: "in_progress", revision: 4, assigned_to: "admin", events: [
      { at: "2026-10-20T09:00:00Z", actor: "admin", action: "claim", summary: "开始处理", commit: "", verification: "", deployed_version: "" },
    ] });
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "执行认领" }));
    expect(await within(screen.getByRole("dialog")).findByText("处理中")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByText("开始处理")).toBeInTheDocument();
    expect(api.mutateFeedbackIssue).toHaveBeenLastCalledWith("admin", "issue-1", expect.objectContaining({ action: "claim", expected_revision: 3 }));
  });

  it("discards one-time plaintext tokens when the section closes", async () => {
    api.createFeedbackToken.mockResolvedValue({ record: { id: "token-1", name: "Codex", created_at: "2026-10-20T00:00:00Z", expires_at: "2026-11-20T00:00:00Z", revoked_at: null }, token: "one-time-test-token" });
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(screen.getByRole("button", { name: "Harness 令牌" }));
    fireEvent.change(screen.getByLabelText("令牌名称"), { target: { value: "Codex" } });
    await userEvent.click(screen.getByRole("button", { name: "创建令牌" }));
    expect(await screen.findByLabelText("一次性令牌")).toHaveValue("one-time-test-token");
    expect(localStorage.getItem("feedback-token")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "关闭令牌管理" }));
    await userEvent.click(screen.getByRole("button", { name: "Harness 令牌" }));
    expect(screen.queryByDisplayValue("one-time-test-token")).not.toBeInTheDocument();
  });

  it("revokes a token and refreshes its server state", async () => {
    const token = { id: "token-1", name: "Codex", created_at: "2026-10-20T00:00:00Z", expires_at: "2026-11-20T00:00:00Z", revoked_at: null };
    api.listFeedbackTokens.mockResolvedValue([token]);
    api.revokeFeedbackToken.mockResolvedValue(undefined);
    render(<AccountPoolFeedbackPanel accessToken="admin" />, { wrapper });
    await userEvent.click(screen.getByRole("button", { name: "Harness 令牌" }));
    await userEvent.click(await screen.findByRole("button", { name: "撤销 Codex" }));
    await waitFor(() => expect(api.revokeFeedbackToken).toHaveBeenCalledWith("admin", "token-1"));
    api.listFeedbackTokens.mockResolvedValue([{ ...token, revoked_at: "2026-10-20T09:00:00Z" }]);
    await userEvent.click(screen.getByRole("button", { name: "关闭令牌管理" }));
    await userEvent.click(screen.getByRole("button", { name: "Harness 令牌" }));
    expect(await screen.findByText("撤销时间：2026-10-20T09:00:00Z")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤销 Codex" })).toBeDisabled();
  });
});
