// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { DATA_CHANGED_EVENT } from "../cacheEvents";
import { createApiClient } from "./client";
import { dashboardFetch } from "./dashboardFetch";

afterEach(() => vi.unstubAllGlobals());

describe("dashboard browser cache policy", () => {
  it("bypasses browser storage for JSON, blobs and native requests while preserving explicit overrides", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const client = createApiClient({ getBaseUrl: () => "https://proxy.test" });
    await client.get("/key/list");
    await client.requestBlob("GET", "/export");
    await dashboardFetch("/model/info");
    expect(fetch.mock.calls.map(([, init]) => init.cache)).toEqual(["no-store", "no-store", "no-store"]);
    await client.get("/public", { cache: "force-cache" });
    expect(fetch).toHaveBeenLastCalledWith(
      "https://proxy.test/public",
      expect.objectContaining({ cache: "force-cache" }),
    );
    await dashboardFetch(new Request("https://proxy.test/public", { cache: "reload" }));
    expect(fetch).toHaveBeenLastCalledWith(expect.any(Request), expect.objectContaining({ cache: "reload" }));
  });

  it("invalidates queries after successful writes, but not reads or rejected writes", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const changed = vi.fn();
    window.addEventListener(DATA_CHANGED_EVENT, changed);
    try {
      await dashboardFetch("/key/list");
      expect(changed).not.toHaveBeenCalled();
      await dashboardFetch("/key/update", { method: "POST" });
      const client = createApiClient({ getBaseUrl: () => "" });
      await client.put("/config", { body: {} });
      expect(changed).toHaveBeenCalledTimes(2);
      fetch.mockResolvedValueOnce(new Response("denied", { status: 403 }));
      await dashboardFetch("/key/update", { method: "POST" });
      expect(changed).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener(DATA_CHANGED_EVENT, changed);
    }
  });
});
