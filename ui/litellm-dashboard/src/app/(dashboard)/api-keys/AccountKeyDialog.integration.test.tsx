import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import { AccountKeyDialog } from "./AccountKeyDialog";

vi.mock("@/components/networking", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  getProxyBaseUrl: () => "http://localhost:4000",
}));

it("creates an account-bound key and invalidates the native key list", async () => {
  vi.mocked(apiClient.get).mockResolvedValue(null);
  vi.mocked(apiClient.post).mockResolvedValue({ key: "sk-preview-once" });
  const client = new QueryClient();
  client.setQueryData(["keys", "list"], { keys: [] });
  render(
    <QueryClientProvider client={client}>
      <AccountKeyDialog accessToken="test" cardId="account-one" name="测试账号" onClose={() => {}} />
    </QueryClientProvider>,
  );
  await userEvent.setup().click(await screen.findByRole("button", { name: "生成 Key" }));
  expect(await screen.findByDisplayValue("sk-preview-once")).toBeInTheDocument();
  expect(apiClient.post).toHaveBeenCalledWith("/account_pool/cards/account-one/key", { accessToken: "test" });
  await waitFor(() => expect(client.getQueryState(["keys", "list"])?.isInvalidated).toBe(true));
});
