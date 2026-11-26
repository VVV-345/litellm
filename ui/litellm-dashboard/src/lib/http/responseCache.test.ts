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
    expect(send).toHaveBeenCalledTimes(2);
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

  it("does not invalidate unrelated HTTP responses when another query polls", async () => {
    await read();
    const query = client.getQueryCache().getAll()[0];
    const options = { queryKey: ["polling"], queryFn: async () => 1 };
    await client.fetchQuery(options);
    await client.refetchQueries({ queryKey: ["polling"] });
    expect(query.state.isInvalidated).toBe(false);
  });

  it("releases raw response bodies after concurrent consumers finish", async () => {
    vi.useFakeTimers();
    try {
      await read();
      await vi.advanceTimersByTimeAsync(1);
      expect(client.getQueryCache().getAll()).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("aborts the underlying request when the sole consumer aborts", async () => {
    let underlyingSignal: AbortSignal | undefined;
    send.mockImplementationOnce(
      (_input, init) =>
        new Promise<Response>((_, reject) => {
          underlyingSignal = init?.signal ?? undefined;
          underlyingSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        }),
    );
    const controller = new AbortController();
    const pending = read({ signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(underlyingSignal?.aborted).toBe(true);
  });

  it("keeps a shared underlying request alive when one of two concurrent consumers aborts", async () => {
    let underlyingSignal: AbortSignal | undefined;
    const deferred = Promise.withResolvers<Response>();
    send.mockImplementationOnce((_input, init) => {
      underlyingSignal = init?.signal ?? undefined;
      return deferred.promise;
    });
    const controllerA = new AbortController();
    const controllerB = new AbortController();
    const reqA = read({ signal: controllerA.signal });
    const reqB = read({ signal: controllerB.signal });
    await Promise.resolve();
    controllerA.abort();
    await expect(reqA).rejects.toMatchObject({ name: "AbortError" });
    expect(underlyingSignal?.aborted).toBe(false);
    deferred.resolve(Response.json({ version: 4 }));
    expect(await (await reqB).json()).toEqual({ version: 4 });
  });
});
