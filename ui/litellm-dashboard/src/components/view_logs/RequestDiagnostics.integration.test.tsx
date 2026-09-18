import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { apiClient } from "@/components/networking";
import { RequestDiagnostics } from "./RequestDiagnostics";

vi.mock("@/components/networking", () => ({ apiClient: { get: vi.fn() } }));

it("links request attempts to the saved body and loads the body only when expanded", async () => {
  const record = {
    event_id: "event-one",
    request_id: "request-one",
    model: "model-a",
    session_id: "session-one",
    incomplete: false,
    truncated: false,
    request: { input: "hello" },
    response: JSON.stringify({
      output: [{ type: "message", content: [{ type: "output_text", text: "saved reply" }] }],
    }),
    result: { input_tokens: 5, output_tokens: 2 },
  };
  vi.mocked(apiClient.get).mockImplementation(async (path) => {
    if (path === "/logs/timing/request-one") return [{ attempt: 1, phase: "upstream_headers", duration_ms: 1234, status: 200 }];
    if (path === "/logs/operations") return { items: [{ event_id: "event-one" }] };
    if (path === "/logs/operations/event-one")
      return {
        attempts: [
          {
            event_id: "attempt-one",
            attempt: 1,
            account_id: "account-one",
            http_status: 502,
            message: "upstream interrupted",
            duration_ms: 1500,
          },
        ],
      };
    if (path === "/logs/full") return { items: [record] };
    if (path === "/logs/full/event-one") return record;
    throw new Error(`Unexpected API: ${path}`);
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RequestDiagnostics accessToken="test" requestId="request-one" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("upstream interrupted")).toBeInTheDocument();
  expect(await screen.findByText("1234 ms · HTTP 200")).toBeInTheDocument();
  expect(screen.getByText(/阶段包含重叠时间/)).toBeInTheDocument();
  expect(apiClient.get).toHaveBeenCalledWith("/logs/operations", {
    accessToken: "test",
    query: { request_id: "request-one", limit: 1 },
  });
  expect(apiClient.get).not.toHaveBeenCalledWith("/logs/full/event-one", expect.anything());
  await userEvent.setup().click(await screen.findByRole("button", { name: "查看完整日志" }));
  expect(await screen.findByText("saved reply")).toBeInTheDocument();
  expect(screen.getByText("hello")).toBeInTheDocument();
  await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith("/logs/full/event-one", { accessToken: "test" }));
});
