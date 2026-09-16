/** 本文件验证版本选取、编辑确认及倒计时结束仍需明确点击的交互。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountPoolReleaseConfirmation } from "./AccountPoolReleaseConfirmation";
import { AccountPoolReleasesPanel } from "./AccountPoolReleasesPanel";
import {
  executeRelease,
  listReleases,
  prepareRelease,
  releaseCommands,
  type ReleaseView,
  type ReleaseJob,
} from "./AccountPoolReleasesApi";

vi.mock("./AccountPoolReleasesApi", () => ({
  executeRelease: vi.fn(),
  listReleases: vi.fn(),
  prepareRelease: vi.fn(),
  releaseCommands: vi.fn(),
}));

const pair = (char: string): NonNullable<ReleaseView["current"]> => ({
  id: char.repeat(24),
  commit: char.repeat(40),
  images: [
    {
      service: "litellm",
      image_id: `sha256:${char.repeat(64)}`,
      revision: char.repeat(40),
      repository: "litellm",
      size: 500,
      digests: [],
    },
    {
      service: "account-pool",
      image_id: `sha256:${char.repeat(64)}`,
      revision: char.repeat(40),
      repository: "manager",
      size: 500,
      digests: [],
    },
  ],
});
const current = pair("b");
const old = pair("a");
const view: ReleaseView = {
  current,
  versions: [
    {
      pair: current,
      current: true,
      note: "稳定版本",
      available: true,
      backup: {
        pair: current,
        created_at: 1700000000,
        archive_bytes: 1000,
        archive_sha256: "hash",
        compose_sha256: "hash",
        schema_fingerprint: "same",
        configuration_source: "running",
      },
    },
    {
      pair: old,
      current: false,
      note: "旧版本备注",
      available: true,
      backup: {
        pair: old,
        created_at: 1600000000,
        archive_bytes: 2000,
        archive_sha256: "hash",
        compose_sha256: "hash",
        schema_fingerprint: "same",
        configuration_source: "imported_current",
      },
    },
  ],
  guide: "原来的回退说明",
  default_guide: "默认说明",
  revision: 3,
  location: "/opt/litellm-releases/backups",
  free_bytes: 1024 ** 3,
  problems: [],
};

describe("project releases", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(listReleases).mockResolvedValue(view);
    vi.mocked(releaseCommands).mockResolvedValue({
      branch: "git switch -c codex/fix-version " + old.commit,
      revert: "git revert --no-commit " + old.commit + "..HEAD",
    });
    vi.mocked(prepareRelease).mockImplementation(async (_, action) => ({
      token: "c".repeat(64),
      action: { text: "", ...action },
      delay_seconds: 0,
      expires_in_seconds: 300,
      current_commit: current.commit,
    }));
    const queuedJob: ReleaseJob = {
      id: "job",
      action: { action: "apply", revision: 3, version_id: old.id, text: "" },
      status: "queued",
      phase: "等待执行",
      message: "",
      created_at: 1000,
      updated_at: 1000,
    };
    vi.mocked(executeRelease).mockResolvedValue(queuedJob);
  });
  afterEach(() => vi.useRealTimers());
  const mount = () =>
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
      >
        <AccountPoolReleasesPanel accessToken="admin" />
      </QueryClientProvider>,
    );

  it("protects the current version and applies the selected archive with explicit confirmation", async () => {
    const user = userEvent.setup();
    mount();
    expect(await screen.findByRole("button", { name: "应用" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "删除" })).toBeDisabled();
    await user.click(screen.getByRole("combobox", { name: "选择备份版本" }));
    await user.click(await screen.findByRole("option", { name: /旧版本备注/ }));
    await user.click(screen.getByRole("button", { name: "应用" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/切换到 aaaaaaaaaa/)).toBeInTheDocument();
    expect(prepareRelease).toHaveBeenCalledWith("admin", { action: "apply", version_id: old.id, revision: 3 });
    expect(executeRelease).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "确认执行" }));
    expect(executeRelease).toHaveBeenCalledWith("admin", "c".repeat(64));
  });

  it("previews editable notes and help before saving", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("button", { name: "编辑备注" }));
    fireEvent.change(screen.getByRole("textbox", { name: "版本备注" }), { target: { value: "验证通过的版本" } });
    await user.click(screen.getByRole("button", { name: "保存备注" }));
    const noteDialog = await screen.findByRole("dialog");
    expect(within(noteDialog).getByText("稳定版本")).toBeInTheDocument();
    expect(within(noteDialog).getByText("验证通过的版本")).toBeInTheDocument();
    const expectedNote = { action: "note", version_id: current.id, text: "验证通过的版本", revision: 3 };
    expect(prepareRelease).toHaveBeenLastCalledWith("admin", expectedNote);
    await user.click(within(noteDialog).getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: "编辑说明" }));
    fireEvent.change(screen.getByRole("textbox", { name: "代码回退说明" }), { target: { value: "我的恢复步骤" } });
    await user.click(screen.getByRole("button", { name: "保存说明" }));
    const guideDialog = await screen.findByRole("dialog");
    expect(within(guideDialog).getByText("我的恢复步骤")).toBeInTheDocument();
    expect(prepareRelease).toHaveBeenLastCalledWith("admin", { action: "guide", text: "我的恢复步骤", revision: 3 });
    expect(executeRelease).not.toHaveBeenCalled();
  });

  it.each([5, 10])("requires a manual click after the %s-second countdown and expires", (delay) => {
    vi.useFakeTimers();
    const confirm = vi.fn();
    render(
      <AccountPoolReleaseConfirmation
        confirmation={{
          token: "token",
          action: { action: delay === 10 ? "delete" : "apply", revision: 0, text: "" },
          delay_seconds: delay,
          expires_in_seconds: 300,
          current_commit: current.commit,
        }}
        title="确认操作"
        description="请核对目标"
        busy={false}
        onClose={vi.fn()}
        onConfirm={confirm}
      />,
    );
    expect(screen.getByRole("button", { name: `确认（${delay} 秒）` })).toBeDisabled();
    act(() => vi.advanceTimersByTime((delay - 1) * 1000));
    expect(screen.getByRole("button", { name: "确认（1 秒）" })).toBeDisabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(300000));
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
  });
});
