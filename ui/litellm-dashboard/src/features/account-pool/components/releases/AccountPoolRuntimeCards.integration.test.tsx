/** 验证镜像核实、单卡及勾选升级只提交所选身份，不触发整站部署。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import { AccountPoolRuntimeCards } from "./AccountPoolRuntimeCards";

vi.mock("@/components/networking", () => ({ apiClient: { get: vi.fn() } }));

it("shows the default and current images, skips current cards and confirms only selected cards", async () => {
  vi.mocked(apiClient.get).mockResolvedValue({
    default_image: "release@sha256:default",
    cards: [
      { environment_id: "one", name: "第一张", status: "current", current_image_id: "same", target_image_id: "same" },
      { environment_id: "two", name: "第二张", status: "update_available", current_image_id: "old", target_image_id: "new" },
    ],
  });
  const prepare = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><AccountPoolRuntimeCards accessToken="fixture" busy={false} prepare={prepare} /></QueryClientProvider>);
  expect(apiClient.get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "核实卡片镜像" }));
  expect(await screen.findByText("release@sha256:default")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "选择卡片 第一张" })).toHaveAttribute("aria-disabled", "true");
  const buttons = screen.getAllByRole("button", { name: "升级此卡" });
  expect(buttons[0]).toBeDisabled();
  fireEvent.click(buttons[1]);
  expect(prepare).toHaveBeenLastCalledWith({ action: "upgrade_cards", card_ids: ["two"] }, expect.stringContaining("不部署整站"));
  fireEvent.click(screen.getByRole("checkbox", { name: "选择卡片 第二张" }));
  fireEvent.click(screen.getByRole("button", { name: "升级所选卡片" }));
  expect(prepare).toHaveBeenLastCalledWith({ action: "upgrade_cards", card_ids: ["two"] }, expect.any(String));
  expect(apiClient.get).toHaveBeenCalledWith("/account_pool/releases/cards", { accessToken: "fixture" });
  client.clear();
});
