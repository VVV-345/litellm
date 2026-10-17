import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useProxyGatewayDelays } from "./useProxyGateways";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import { DATA_CHANGED_EVENT } from "@/lib/cacheEvents";
import { createApiClient } from "@/lib/http/client";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("reuses fresh measurements on remount and only invalidates delay data", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => Response.json([]));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const key = accountPoolQueryKeys.proxyGatewayDelays("test");
  client.setQueryData(key, []);
  client.setQueryData(["other-page"], { value: 1 });
  const invalidate = () => void client.invalidateQueries({ refetchType: "none" });
  window.addEventListener(DATA_CHANGED_EVENT, invalidate);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const first = renderHook(() => useProxyGatewayDelays("test", true), { wrapper });
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(fetch).not.toHaveBeenCalled();
  first.unmount();
  const second = renderHook(() => useProxyGatewayDelays("test", true), { wrapper });
  await act(() => vi.advanceTimersByTimeAsync(60_100));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(client.getQueryState(["other-page"])?.isInvalidated).toBe(false);
  await act(async () => {
    await createApiClient({ getBaseUrl: () => "" }).post("/test-mutation");
  });
  expect(client.getQueryState(["other-page"])?.isInvalidated).toBe(true);
  second.unmount();
  window.removeEventListener(DATA_CHANGED_EVENT, invalidate);
  client.clear();
});

it("does not retry a failed measurement every second", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => Response.json({ error: "offline" }, { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(() => useProxyGatewayDelays("test", true), { wrapper });
  await act(() => vi.advanceTimersByTimeAsync(100));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(59_000));
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(fetch).toHaveBeenCalledTimes(2);
  view.unmount();
  client.clear();
});
