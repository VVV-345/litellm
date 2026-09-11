/** 本文件验证供应商家族目录的实例计数、能力状态和创建入口。 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolProviderFamilies } from "./AccountPoolProviderFamilies";

const listFamilies = vi.fn();

vi.mock("./AccountPoolApi", () => ({
  listAccountPoolProviderFamilies: (...args: unknown[]) => listFamilies(...args),
}));

describe("AccountPoolProviderFamilies", () => {
  beforeEach(() => {
    listFamilies.mockReset();
    listFamilies.mockResolvedValue([
      {
        kind: "openai_codex",
        display_name: "Codex",
        supplier: "openai_codex",
        authentication: "OAuth",
        available: true,
        description: "Codex OAuth",
        card_count: 2,
      },
      {
        kind: "openai_compatible",
        display_name: "OpenAI 兼容",
        supplier: "openai_compatible",
        authentication: "Base URL + API Key",
        available: true,
        description: "OpenAI 兼容直连",
        card_count: 3,
      },
    ]);
  });

  it("shows instance counts and only enables creation for an available family", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AccountPoolProviderFamilies accessToken="token" onCreate={onCreate} />
      </QueryClientProvider>,
    );

    const codex = await screen.findByText("Codex");
    const codexCard = codex.closest('[data-slot="card"]') as HTMLElement | null;
    expect(codexCard).not.toBeNull();
    expect(within(codexCard!).getByText(/2/)).toBeInTheDocument();
    await user.click(within(codexCard!).getByRole("button", { name: /新建|Create/i }));
    expect(onCreate).toHaveBeenCalledWith("openai_codex");

    const compatible = screen.getByText("OpenAI 兼容").closest('[data-slot="card"]') as HTMLElement | null;
    expect(compatible).not.toBeNull();
    expect(within(compatible!).getByText(/3/)).toBeInTheDocument();
    await user.click(within(compatible!).getByRole("button", { name: /新建|Create/i }));
    expect(onCreate).toHaveBeenCalledWith("openai_compatible");
  });
});
