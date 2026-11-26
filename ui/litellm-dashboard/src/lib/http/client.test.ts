import { describe, it, expect, vi } from "vitest";
import {
  createApiClient,
  ApiError,
  deriveErrorMessage,
  extractProxyErrorMessage,
  unwrapProxyErrorMessage,
} from "./client";

const okResponse = (data: unknown): Response =>
  ({ ok: true, status: 200, text: async () => JSON.stringify(data) }) as unknown as Response;

const emptyResponse = (status: number): Response => ({ ok: true, status, text: async () => "" }) as unknown as Response;

const errorResponse = (status: number, body: unknown): Response =>
  ({ ok: false, status, text: async () => JSON.stringify(body) }) as unknown as Response;

const rawErrorResponse = (status: number, text: string): Response =>
  ({ ok: false, status, text: async () => text }) as unknown as Response;

describe("createApiClient", () => {
  it("builds the URL from base + path + query and sets the auth + JSON headers", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => okResponse({ ok: true }));
    const client = createApiClient({
      getBaseUrl: () => "https://proxy.example",
      getAuthHeaderName: () => "x-litellm-key",
      fetchImpl,
    });

    const result = await client.get("/models", { accessToken: "sk-123", query: { team: "t1", page: 2 } });

    expect(result).toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("https://proxy.example/models?team=t1&page=2");
    expect(init).toMatchObject({ method: "GET" });
    expect(init?.headers).toEqual({
      "Content-Type": "application/json",
      "x-litellm-key": "Bearer sk-123",
    });
    expect(init?.body).toBeUndefined();
  });

  it("JSON-serializes the body for writes", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => okResponse({}));
    const client = createApiClient({ getBaseUrl: () => "", fetchImpl });

    await client.post("/model/new", { accessToken: "sk", body: { model_name: "gpt" } });

    const [, init] = fetchImpl.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ model_name: "gpt" }));
  });

  it("throws ApiError with the derived message and invokes onError on a non-2xx response", async () => {
    const fetchImpl = vi.fn(async () => errorResponse(403, { error: { message: "no access" } }));
    const onError = vi.fn();
    const client = createApiClient({ getBaseUrl: () => "", onError, fetchImpl });

    const promise = client.get("/keys", { accessToken: "sk" });

    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ message: "no access", status: 403 });
    expect(onError).toHaveBeenCalledWith({ message: "no access", status: 403, sessionToken: null });
  });

  it("unwraps an object-shaped detail ({detail:{error}}) rather than dumping the JSON envelope (FastAPI HTTPException shape)", async () => {
    const conflict = "A skill named 'gitlab' already exists. Update the existing skill instead of adding it again.";
    const fetchImpl = vi.fn(async () => errorResponse(409, { detail: { error: conflict } }));
    const onError = vi.fn();
    const client = createApiClient({ getBaseUrl: () => "", onError, fetchImpl });

    const promise = client.get("/claude-code/plugins", { accessToken: "sk" });

    await expect(promise).rejects.toMatchObject({ message: conflict, status: 409 });
    expect(onError).toHaveBeenCalledWith({ message: conflict, status: 409, sessionToken: null });
  });

  it("falls back to the raw text body when a non-2xx response is not JSON (e.g. an HTML 502)", async () => {
    const fetchImpl = vi.fn(async () => rawErrorResponse(502, "<html>Bad Gateway</html>"));
    const onError = vi.fn();
    const client = createApiClient({ getBaseUrl: () => "", onError, fetchImpl });

    const promise = client.get("/keys", { accessToken: "sk" });

    await expect(promise).rejects.toMatchObject({ message: "<html>Bad Gateway</html>", status: 502 });
    expect(onError).toHaveBeenCalledWith({ message: "<html>Bad Gateway</html>", status: 502, sessionToken: null });
  });

  it("returns undefined for an empty success body (e.g. a 204 No Content)", async () => {
    const fetchImpl = vi.fn(async () => emptyResponse(204));
    const client = createApiClient({ getBaseUrl: () => "", fetchImpl });

    await expect(client.delete("/policies/abc", { accessToken: "sk" })).resolves.toBeUndefined();
  });

  it("omits the auth header when no token is provided", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => okResponse({}));
    const client = createApiClient({ getBaseUrl: () => "", getAuthHeaderName: () => "Authorization", fetchImpl });

    await client.get("/public/info");

    const [, init] = fetchImpl.mock.calls[0];
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("resolves the global fetch per call, so a swap after construction takes effect", async () => {
    const client = createApiClient({ getBaseUrl: () => "" });

    const original = globalThis.fetch;
    const swapped = vi.fn(async () => okResponse({ swapped: true }));
    globalThis.fetch = swapped as unknown as typeof fetch;
    try {
      const result = await client.get("/ping", { accessToken: "sk" });
      expect(result).toEqual({ swapped: true });
      expect(swapped).toHaveBeenCalledTimes(1);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("aborts stalled GET requests after 35s by default without leaving timers after normal responses", async () => {
    vi.useFakeTimers();
    try {
      const fastClient = createApiClient({ getBaseUrl: () => "", fetchImpl: async () => okResponse({ ok: true }) });
      await expect(fastClient.get("/fast")).resolves.toEqual({ ok: true });
      expect(vi.getTimerCount()).toBe(0);

      let requestSignal: AbortSignal | undefined;
      const stalledFetch = vi.fn<typeof fetch>(
        (_input, init) =>
          new Promise<Response>((_, reject) => {
            requestSignal = init?.signal ?? undefined;
            requestSignal?.addEventListener("abort", () => reject(requestSignal?.reason), { once: true });
          }),
      );
      const stalledClient = createApiClient({ getBaseUrl: () => "", fetchImpl: stalledFetch });
      const pending = stalledClient.get("/stalled");
      const caught = pending.catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(34_999);
      expect(requestSignal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      const error = await caught;
      expect(requestSignal?.aborted).toBe(true);
      expect(error).toMatchObject({ name: "TimeoutError" });
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not apply the default 35s timeout to writes or when timeoutMs is null", async () => {
    vi.useFakeTimers();
    try {
      const deferredPost = Promise.withResolvers<Response>();
      const deferredGet = Promise.withResolvers<Response>();
      const fetchImpl = vi
        .fn<typeof fetch>()
        .mockImplementationOnce(() => deferredPost.promise)
        .mockImplementationOnce(() => deferredGet.promise);
      const client = createApiClient({ getBaseUrl: () => "", fetchImpl });
      const write = client.post("/slow-write", { body: {} });
      const read = client.get("/slow-read", { timeoutMs: null });
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchImpl.mock.calls[0]?.[1]?.signal).toBeUndefined();
      expect(fetchImpl.mock.calls[1]?.[1]?.signal).toBeUndefined();
      deferredPost.resolve(okResponse({ written: true }));
      deferredGet.resolve(okResponse({ read: true }));
      await expect(write).resolves.toEqual({ written: true });
      await expect(read).resolves.toEqual({ read: true });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("deriveErrorMessage", () => {
  it("extracts error.message from a ProxyException body, the shape the proxy emits for a pre-call hook HTTPException", () => {
    const actionable =
      "MCP semantic tool filtering could not run: embedding model 'text-embedding-3-small' exceeded its context window while embedding the user query. The request was blocked instead of silently passing all tools through. Switch to an embedding model with a larger context window, or disable semantic tool filtering.";
    const wireBody = {
      error: { message: actionable, type: "None", param: "None", code: "400" },
    };
    expect(deriveErrorMessage(wireBody)).toBe(actionable);
  });

  it("returns error directly when it is a plain string", () => {
    expect(deriveErrorMessage({ error: "flat error text" })).toBe("flat error text");
  });

  it("falls back to a string detail field", () => {
    expect(deriveErrorMessage({ detail: "detail text" })).toBe("detail text");
  });

  it("unwraps the HTTPException detail.error shape management endpoints raise", () => {
    expect(deriveErrorMessage({ detail: { error: "Team(s) not found: ghost-team" } })).toBe(
      "Team(s) not found: ghost-team",
    );
  });
});

describe("unwrapProxyErrorMessage", () => {
  it("should unwrap the proxy's stringified python dict message", () => {
    expect(
      unwrapProxyErrorMessage("{'error': 'Cost center CC-9999 is not recognized. Contact the FinOps team.'}"),
    ).toBe("Cost center CC-9999 is not recognized. Contact the FinOps team.");
  });

  it("should unwrap the full JSON error envelope down to the inner message", () => {
    const envelope = JSON.stringify({
      error: {
        message: "{'error': 'cost_center is required in team metadata. Contact the FinOps team.'}",
        type: "internal_server_error",
        param: "None",
        code: "400",
      },
    });
    expect(unwrapProxyErrorMessage(envelope)).toBe(
      "cost_center is required in team metadata. Contact the FinOps team.",
    );
  });

  it("should return plain messages and unparseable input unchanged", () => {
    expect(unwrapProxyErrorMessage("Failed to fetch")).toBe("Failed to fetch");
    expect(unwrapProxyErrorMessage("{}")).toBe("{}");
  });
});

describe("extractProxyErrorMessage", () => {
  it("should unwrap an Error's message without the error name prefix", () => {
    const error = new ApiError("{'error': 'Cost center CC-9999 is not recognized.'}", 400, {});
    expect(extractProxyErrorMessage(error)).toBe("Cost center CC-9999 is not recognized.");
  });

  it("should stringify non-Error inputs", () => {
    expect(extractProxyErrorMessage("plain failure")).toBe("plain failure");
  });
});
