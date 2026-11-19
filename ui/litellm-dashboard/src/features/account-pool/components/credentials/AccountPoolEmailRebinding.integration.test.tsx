/** 验证网页导入不启动换绑，用户勾选确认后才开始任务，失败显示原进度。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountPoolEmailRebinding, BindingPanel } from "./AccountPoolEmailRebinding";
import * as api from "../../api/AccountPoolEmailRebindingApi";
import { getAutomation } from "../../api/AccountPoolAutomationApi";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";

vi.mock("../../api/AccountPoolEmailRebindingApi", () => ({
  getEmailBindings: vi.fn(),
  uploadEmailPool: vi.fn(),
  proposeEmailBinding: vi.fn(),
  confirmEmailBinding: vi.fn(),
  actOnEmailBinding: vi.fn(),
  exportEmailBindingStatus: vi.fn(),
  openEmailBindingScreen: vi.fn(),
  provideEmailBindingCode: vi.fn(),
}));
vi.mock("../../api/AccountPoolAutomationApi", () => ({ getAutomation: vi.fn() }));
const environment = { id: "card-1", name: "fixture", status: "ready", auth_file_bound: true } as AccountPoolEnvironment;
const mailbox = { id: "mail-1", email: "new@example.test", method: "manual", status: "available" } as const;
const job = {
  id: "job-1",
  environment_id: environment.id,
  mailbox_id: mailbox.id,
  old_email: "old@example.test",
  target_email: mailbox.email,
  stage: "awaiting_confirmation",
  email_changed: false,
  irreversible_started: false,
  version: 0,
  message: "请核对换绑资料",
  created_at: "2026-11-19T00:00:00Z",
  updated_at: "2026-11-19T00:00:00Z",
  confirmation_expires_at: "2026-11-19T00:15:00Z",
} as const;
function renderPanel() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <BindingPanel accessToken="fixture-admin" environment={environment} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAutomation).mockResolvedValue({
    version: 0,
    checked_at: null,
    image_checked: true,
    profiles: [],
    jobs: [],
    settings: { enabled: true, total_timeout_seconds: 600, human_timeout_seconds: 300 },
  });
  vi.mocked(api.getEmailBindings).mockResolvedValue({ version: 0, mailboxes: [mailbox], jobs: [] });
});
it("imports a web-selected TXT without proposing or starting a rebind", async () => {
  vi.mocked(api.uploadEmailPool).mockResolvedValue({
    added: 1,
    existing: 0,
    view: { version: 1, mailboxes: [mailbox], jobs: [] },
  });
  renderPanel();
  const content = "new@example.test----fixture-mail-password----client----fixture-mail-rt";
  const selected = Object.defineProperty(new File([content], "mailboxes.txt"), "text", { value: async () => content });
  fireEvent.change(screen.getByLabelText("上传目标邮箱 CSV / TXT"), { target: { files: [selected] } });
  fireEvent.click(screen.getByRole("button", { name: "导入邮箱池" }));
  await screen.findByText(/新增 1 条/);
  expect(api.uploadEmailPool).toHaveBeenCalledWith("fixture-admin", content, "mailboxes.txt");
  expect(api.proposeEmailBinding).not.toHaveBeenCalled();
  expect(api.confirmEmailBinding).not.toHaveBeenCalled();
  expect(screen.queryByText(/fixture-mail-password/)).not.toBeInTheDocument();
});
it("cannot start before explicit confirmation and sends the displayed operation", async () => {
  vi.mocked(api.getEmailBindings).mockResolvedValue({
    version: 1,
    mailboxes: [{ ...mailbox, status: "reserved" }],
    jobs: [job],
  });
  vi.mocked(api.confirmEmailBinding).mockResolvedValue({ ...job, stage: "draining" });
  renderPanel();
  const button = await screen.findByRole("button", { name: "确认并开始换绑" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(api.confirmEmailBinding).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认将此卡/ }));
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await screen.findByRole("button", { name: "确认并开始换绑" });
  expect(api.confirmEmailBinding).toHaveBeenCalledWith("fixture-admin", job);
});
it("keeps failed post-change progress and exposes only authorization retry", async () => {
  vi.mocked(api.getEmailBindings).mockResolvedValue({
    version: 2,
    mailboxes: [{ ...mailbox, status: "bound" }],
    jobs: [
      {
        ...job,
        stage: "verification_failed",
        email_changed: true,
        irreversible_started: true,
        message: "换绑完成，授权失败，原卡保留",
      },
    ],
  });
  renderPanel();
  await screen.findByText("换绑完成，授权失败，原卡保留");
  expect(screen.getByRole("button", { name: "仅重试授权与验收" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "确认并开始换绑" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "取消，释放未使用邮箱" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "准备换绑，先核对资料" })).toBeDisabled();
});

it("can reopen preserved progress even when reauthorization temporarily unbinds credentials", async () => {
  vi.mocked(api.getEmailBindings).mockResolvedValue({
    version: 2,
    mailboxes: [{ ...mailbox, status: "bound" }],
    jobs: [
      {
        ...job,
        stage: "verification_failed",
        email_changed: true,
        irreversible_started: true,
        message: "邮箱已绑定，等待重新授权",
      },
    ],
  });
  const failed = { ...environment, status: "error" as const, auth_file_bound: false };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountPoolEmailRebinding accessToken="fixture-admin" environment={failed} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "换绑邮箱 fixture" }));
  await screen.findByText("邮箱已绑定，等待重新授权");
  expect(screen.getByRole("button", { name: "仅重试授权与验收" })).toBeVisible();
});
