/** 本文件验证号池供应商使用对应的内置厂商图标。 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccountPoolSupplierLogo } from "./AccountPoolSupplierLogo";

vi.mock("@/components/molecules/models/ProviderLogo", () => ({
  ProviderLogo: ({ provider }: { provider: string }) => <span data-testid="supplier-logo">{provider}</span>,
}));

describe("AccountPoolSupplierLogo", () => {
  it.each([
    ["glm", "GLM logo", /zai/],
    ["opencode", "OpenCode logo", /opencode-light/],
  ] as const)("renders a real bundled image for %s", (supplier, label, file) => {
    render(<AccountPoolSupplierLogo supplier={supplier} />);
    expect(screen.getAllByRole("img", { name: label }).some((image) => file.test(image.getAttribute("src") ?? ""))).toBe(true);
  });

  it.each([
    ["openai_compatible", "openai"],
    ["openai_codex", "openai"],
    ["anthropic_claude", "anthropic"],
    ["google_antigravity", "gemini"],
    ["kimi", "moonshot"],
    ["xai", "xai"],
    ["vertex", "vertex_ai"],
  ] as const)("maps %s to %s", (supplier, provider) => {
    render(<AccountPoolSupplierLogo supplier={supplier} />);

    expect(screen.getByTestId("supplier-logo")).toHaveTextContent(provider);
  });
});
