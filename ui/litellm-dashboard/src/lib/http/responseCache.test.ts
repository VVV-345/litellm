import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cachedDashboardRequest, registerResponseCache } from "./responseCache";

describe("dashboard response cache", () => {
  let client: QueryClient;
  let unregister: () => void;
  const headers = { Authorization: "Bearer test-session" };
  const send = vi.fn<typeof fetch>();
  const read = (init: RequestInit = {}) =>
    cachedDashboardRequest("https://proxy.test/key/list", { headers, ...init }, send);

  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    unregister = registerResponseCache(client);
    send.mockReset().mockImplementation(async () => Response.json({ version: 1 }));
  });
  afterEach(() => {
    unregister();
    client.clear();
  });

  it("shares concurrent reads and returns independently readable bodies", async () => {
    const responses = await Promise.all([read(), read(), read()]);
    expect(send).toHaveBeenCalledOnce();
    expect(await Promise.all(responses.map((response) => response.json()))).toEqual(Array(3).fill({ version: 1 }));
    expect(await (await read()).json()).toEqual({ version: 1 });
    expect(send).toHaveBeenCalledOnce();
  });

  it("does not mark a response ready until the entire body is available", async () => {
    const body = new TransformStream();
    const writer = body.writable.getWriter();
    send.mockResolvedValueOnce(new Response(body.readable, { headers: { "Content-Type": "application/json" } }));
    const ready = vi.fn();
    const pending = read().then((response) => {
      ready();
      return response;
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(ready).not.toHaveBeenCalled();
    await writer.write(new TextEncoder().encode('{"complete":true}'));
    await writer.close();
    expect(await (await pending).json()).toEqual({ complete: true });
  });

  it("replaces cached data after invalidation and retains it if an update fails", async () => {
    await read();
    await client.invalidateQueries({ queryKey: ["dashboard-http"], refetchType: "none" });
    send.mockResolvedValueOnce(Response.json({ version: 2 }));
    expect(await (await read()).json()).toEqual({ version: 2 });
    await client.invalidateQueries({ queryKey: ["dashboard-http"], refetchType: "none" });
    send.mockResolvedValueOnce(Response.json({ error: "offline" }, { status: 503 }));
    expect((await read()).status).toBe(503);
    expect(client.getQueryCache().getAll()[0].state.data).toBeDefined();
    send.mockResolvedValueOnce(Response.json({ version: 3 }));
    expect(await (await read()).json()).toEqual({ version: 3 });
  });

  it("uses the network for explicit reloads, mutations and one-time authorization claims", async () => {
    await read();
    await read({ cache: "reload" });
    await read({ cache: "no-store" });
    await read({ method: "POST" });
    await cachedDashboardRequest("https://proxy.test/api/plugins/auth-token", { headers }, send);
    await cachedDashboardRequest("https://proxy.test/api/plugins/auth-token", { headers }, send);
    expect(send).toHaveBeenCalledTimes(6);
  });

  it("isolates different credentials and prevents old in-flight requests populating a new session", async () => {
    const pending = Promise.withResolvers<Response>();
    send.mockReturnValueOnce(pending.promise);
    const oldRead = read();
    unregister();
    client.clear();
    const next = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
    unregister = registerResponseCache(next);
    expect(await (await read({ headers: { Authorization: "Bearer new-session" } })).json()).toEqual({ version: 1 });
    pending.resolve(Response.json({ version: "old" }));
    await oldRead.catch(() => {});
    expect(next.getQueryCache().getAll()).toHaveLength(1);
    expect(await (await read({ headers: { Authorization: "Bearer new-session" } })).json()).toEqual({ version: 1 });
    unregister();
    next.clear();
  });

  it("does not serve stale HTTP data when a data query is explicitly refetched", async () => {
    const options = { queryKey: ["keys"], queryFn: async () => (await read()).json() };
    await client.fetchQuery(options);
    send.mockResolvedValueOnce(Response.json({ version: 2 }));
    await client.refetchQueries({ queryKey: ["keys"], exact: true });
    expect(client.getQueryData(["keys"])).toEqual({ version: 2 });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("returns expired cached data immediately while a background update replaces it", async () => {
    await read();
    const query = client.getQueryCache().getAll()[0];
    client.setQueryData(query.queryKey, query.state.data, { updatedAt: Date.now() - 10 * 60_000 });
    const pending = Promise.withResolvers<Response>();
    send.mockReturnValueOnce(pending.promise);
    expect(await (await read()).json()).toEqual({ version: 1 });
    expect(send).toHaveBeenCalledTimes(2);
    pending.resolve(Response.json({ version: 2 }));
    await vi.waitFor(() => expect(query.state.fetchStatus).toBe("idle"));
    expect(await (await read()).json()).toEqual({ version: 2 });
  });
});
