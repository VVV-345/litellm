/** 验证网页只把助手 Key 发往回环 API，并检查启动凭证与失败结果。 */
import { describe, expect, it, vi } from "vitest";
import { CamoufoxClient, CamoufoxError, sameProxy } from "./CamoufoxClient";
const proxy = { proxy_type: "socks5" as const, host: "isp.example", port: 1080, username: "alice", password: "secret" };
describe("Camoufox client", () => {
  it.each([
    "https://remote.example:10109",
    "http://127.0.0.1.evil:10109",
    "http://user:key@localhost:10109",
    "http://localhost:10109/path",
    "http://localhost:10109?key=secret",
  ])("rejects unsafe address %s", (url) => {
    expect(() => new CamoufoxClient(url, "key")).toThrow(CamoufoxError);
  });
  it("passes the selected ISP to the helper and opens only with its returned ticket", async () => {
    const transport = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toMatch(/^http:\/\/127\.0\.0\.1:10109\/v1\//);
      expect(init?.headers).toEqual({ Authorization: "Bearer local-key", "Content-Type": "application/json" });
      expect(init?.redirect).toBe("error");
      expect(init?.credentials).toBe("omit");
      const body = JSON.parse(String(init?.body));
      if (String(input).endsWith("/prepare")) {
        expect(body).toEqual({ card_id: "card", proxy });
        return new Response(JSON.stringify({ ticket: "a".repeat(64), proxy_id: "b".repeat(64), imported: false }));
      }
      expect(body).toEqual({ ticket: "a".repeat(64), url: "https://auth.openai.com/oauth/authorize" });
      return new Response(JSON.stringify({ opened: true }));
    });
    const client = new CamoufoxClient("http://127.0.0.1:10109", "local-key", transport);
    const prepared = await client.prepare(proxy, "card");
    expect(prepared.imported).toBe(false);
    await client.open(prepared.ticket, "https://auth.openai.com/oauth/authorize");
  });
  it("hides unexpected response and transport secrets", async () => {
    const client = new CamoufoxClient(
      "http://localhost:10109",
      "key",
      async () => new Response(JSON.stringify({ code: "password=secret" }), { status: 400 }),
    );
    await expect(client.testConnection()).rejects.toMatchObject({ code: "request", message: "request" });
    const failed = new CamoufoxClient("http://localhost:10109", "key", async () => {
      throw new Error("key password=secret");
    });
    await expect(failed.testConnection()).rejects.toMatchObject({ code: "connection", message: "connection" });
  });
  it("detects credential and protocol changes while normalizing host case", () => {
    expect(sameProxy(proxy, { ...proxy, host: "ISP.EXAMPLE." })).toBe(true);
    expect(sameProxy(proxy, { ...proxy, password: "new" })).toBe(false);
    expect(sameProxy(proxy, { ...proxy, proxy_type: "http" })).toBe(false);
  });
  it("sends login credentials only to the local helper and forwards cancellation", async () => {
    const controller = new AbortController();
    const transport = vi.fn<typeof fetch>(async (input, init) => {
      expect(String(input)).toBe("http://127.0.0.1:10109/v1/login");
      expect(JSON.parse(String(init?.body))).toEqual({ ticket: "a".repeat(64), credentials: "private-record", allow_consent: true });
      expect(init?.cache).toBe("no-store");
      controller.abort();
      expect(init?.signal?.aborted).toBe(true);
      return new Response(JSON.stringify({ status: "stopped" }));
    });
    const client = new CamoufoxClient("http://127.0.0.1:10109", "key", transport);
    expect(await client.login("a".repeat(64), "private-record", true, controller.signal)).toEqual({ status: "stopped" });
  });
});
