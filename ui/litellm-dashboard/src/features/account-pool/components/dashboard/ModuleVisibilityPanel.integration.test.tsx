// 验证模块开关在保存及重新读取期间锁定，保存完成后仍可继续编辑。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ModuleVisibilityPanel } from "./ModuleVisibilityPanel";

afterEach(() => vi.unstubAllGlobals());

it("locks module toggles until save and refetch finish, then preserves the next edit", async () => {
  await i18n.changeLanguage("zh-CN");
  let disabled = ["agents", "workflows"];
  let releaseUpdate!: () => void;
  let releaseRefetch!: () => void;
  const updatePending = new Promise<void>((resolve) => {
    releaseUpdate = resolve;
  });
  const refetchPending = new Promise<void>((resolve) => {
    releaseRefetch = resolve;
  });
  const updates: unknown[] = [];
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path === "/get/ui_settings") {
        reads += 1;
        if (reads === 2) await refetchPending;
        return Response.json({ values: { disabled_ui_pages: disabled } });
      }
      if (path === "/update/ui_settings" && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        updates.push(body);
        if (updates.length === 1) await updatePending;
        disabled = body.disabled_ui_pages;
        return Response.json({ values: { disabled_ui_pages: disabled } });
      }
      throw new Error(`Unexpected request: ${init?.method} ${path}`);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ModuleVisibilityPanel accessToken="admin-token" />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const agents = await screen.findByRole("checkbox", { name: "Agents" });
  const workflows = screen.getByRole("checkbox", { name: "Workflow Runs" });
  await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).toBeEnabled());
  await user.click(agents);
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(agents).toHaveAttribute("aria-disabled", "true");
  await user.click(workflows);
  expect(workflows).not.toBeChecked();
  await act(async () => {
    releaseUpdate();
  });
  expect(screen.getByRole("button", { name: "正在保存" })).toBeDisabled();
  expect(workflows).toHaveAttribute("aria-disabled", "true");
  await act(async () => {
    releaseRefetch();
  });
  expect(await screen.findByText("已保存，侧边栏已刷新")).toBeInTheDocument();
  expect(agents).toBeChecked();
  expect(workflows).not.toBeChecked();
  fireEvent.click(workflows);
  expect(workflows).toBeChecked();
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByText("已保存，侧边栏已刷新")).toBeInTheDocument();
  expect(updates).toEqual([{ disabled_ui_pages: ["workflows"] }, { disabled_ui_pages: [] }]);
  client.clear();
});
