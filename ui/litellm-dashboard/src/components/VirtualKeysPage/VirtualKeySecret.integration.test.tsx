import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import { VirtualKeySecret } from "./VirtualKeySecret";

vi.mock("@/components/networking", () => ({ apiClient: { post: vi.fn() } }));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({ default: () => ({ accessToken: "admin-session" }) }));
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn() } }));
const plaintext = "sk-example-only-for-test";
const token = "a".repeat(64);
const writeText = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.post).mockResolvedValue({ key: plaintext });
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
});
afterEach(() => vi.useRealTimers());

it("keeps the secret out of the initial page and copies the complete value while masked", async () => {
  render(<VirtualKeySecret token={token} />);
  expect(screen.getByLabelText("虚拟密钥")).toHaveTextContent("****************");
  expect(apiClient.post).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "复制密钥" })));
  expect(writeText).toHaveBeenCalledWith(plaintext);
  expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledWith(
    "/key/reveal",
    expect.objectContaining({ accessToken: "admin-session", body: { token } }),
  );
});

it("reveals only on demand and masks again after 30 seconds", async () => {
  vi.useFakeTimers();
  render(<VirtualKeySecret token={token} />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "显示密钥" })));
  expect(screen.getByLabelText("虚拟密钥")).toHaveTextContent(plaintext);
  act(() => vi.advanceTimersByTime(30000));
  expect(screen.getByLabelText("虚拟密钥")).toHaveTextContent("****************");
});

it("explains unavailable old keys and never copies their hash", async () => {
  vi.mocked(apiClient.post).mockRejectedValue(new Error("旧密钥只保存了哈希"));
  render(<VirtualKeySecret token={token} />);
  fireEvent.click(screen.getByRole("button", { name: "复制密钥" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("旧密钥只保存了哈希");
  expect(writeText).not.toHaveBeenCalled();
});

it("clears revealed secrets when switching to another key", async () => {
  const view = render(<VirtualKeySecret token={token} />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "显示密钥" })));
  view.rerender(<VirtualKeySecret token={"b".repeat(64)} />);
  expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
});
