import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import DashboardRecovery from "./DashboardRecovery";
import DashboardError from "@/app/(dashboard)/error";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";

function Draft() {
  useUnsavedChanges(true);
  return <input aria-label="Draft" defaultValue="unsaved value" />;
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("reports a changed build on foreground return without reloading or discarding a draft", async () => {
  await i18n.changeLanguage("zh-CN");
  const meta = document.createElement("meta");
  meta.name = "litellm-ui-build";
  meta.content = "old-build";
  document.head.append(meta);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response('<html><head><meta name="litellm-ui-build" content="new-build"></head></html>')),
  );
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const { unmount } = render(
    <>
      <DashboardRecovery />
      <Draft />
    </>,
  );
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByRole("alert")).toHaveTextContent("新版本");
  fireEvent.click(screen.getByRole("button", { name: "更新页面" }));
  expect(confirm).toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("unsaved value");
  const leaving = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(leaving);
  expect(leaving.defaultPrevented).toBe(true);
  unmount();
  meta.remove();
});

it("offers retry for failed scripts without catching unrelated application errors", async () => {
  await i18n.changeLanguage("zh-CN");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<><DashboardRecovery /><Draft /></>);
  fireEvent(window, new ErrorEvent("error", { message: "ordinary API failure" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent(window, new ErrorEvent("error", { message: "ChunkLoadError: Loading chunk 42 failed" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("模块加载失败");
  fireEvent.click(screen.getByRole("button", { name: "重试加载" }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("unsaved value");
});

it("uses a confirmed document reload for failed lazy modules instead of resetting their cached rejection", async () => {
  await i18n.changeLanguage("zh-CN");
  const reset = vi.fn();
  const reload = vi.fn();
  const { unmount } = render(<DashboardError error={new Error("ChunkLoadError: Loading chunk 42 failed")} reset={reset} />);
  const originalWindow = window;
  vi.stubGlobal("window", Object.create(originalWindow, { location: { value: { reload } } }));
  fireEvent.click(screen.getByRole("button", { name: "重试加载" }));
  expect(reload).toHaveBeenCalledOnce();
  expect(reset).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
  unmount();
});

it("keeps the normal reset action for errors unrelated to module loading", async () => {
  await i18n.changeLanguage("zh-CN");
  const reset = vi.fn();
  render(<DashboardError error={new Error("ordinary API failure")} reset={reset} />);
  fireEvent.click(screen.getByRole("button", { name: "重试加载" }));
  expect(reset).toHaveBeenCalledOnce();
});
