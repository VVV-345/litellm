import test from "node:test";
import assert from "node:assert/strict";
import { authorizeAccount } from "./authorize.mjs";

const options = {
  dashboard: "http://127.0.0.1:4001", helper: "http://127.0.0.1:10109",
  accessToken: "admin-secret", helperKey: "local-secret", cardId: "e5304d0c-462d-46c5-a738-7c3fc31de8ec",
  credentials: "test@example.test----pw----GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", allowConsent: true,
};
const proxy = { proxy_type: "socks5", host: "isp.example", port: 1080, username: "u", password: "p" };
test("prepares ISP before fresh OAuth, rechecks it and sends credentials only to the helper", async () => {
  const requests = [];
  const transport = async (url, init) => {
    const path = new URL(url).pathname;
    requests.push(path);
    assert.equal(init.redirect, "error");
    assert.equal(init.cache, "no-store");
    if (new URL(url).port === "4001") {
      assert.equal(init.headers.Authorization, "Bearer admin-secret");
      assert.equal(String(init.body).includes(options.credentials), false);
    } else assert.equal(init.headers.Authorization, "Bearer local-secret");
    if (path.endsWith("/browser-proxy")) return Response.json(proxy);
    if (path.endsWith("/prepare")) return Response.json({ ticket: "a".repeat(64), imported: false });
    if (path.endsWith("/authorize")) return Response.json({ flow: "browser_oauth", authorization_url: "https://auth.openai.com/oauth/authorize?state=1234567890abcdef&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" });
    if (path.endsWith("/open")) return Response.json({ opened: true });
    assert.deepEqual(JSON.parse(init.body), { ticket: "a".repeat(64), credentials: options.credentials, allow_consent: true });
    return Response.json({ status: "callback_received" });
  };
  assert.deepEqual(await authorizeAccount(options, transport), { status: "callback_received" });
  assert.deepEqual(requests.map((p) => p.split('/').at(-1)), ['browser-proxy', 'prepare', 'authorize', 'browser-proxy', 'open', 'login']);
});
test("rejects unsafe helper addresses and malformed credentials before sending anything", async () => {
  for (const invalid of [{ helper: 'https://evil.example' }, { credentials: 'bad' }]) {
    await assert.rejects(authorizeAccount({ ...options, ...invalid }, async () => assert.fail('must not send')));
  }
});
test("a changed ISP prevents opening and remote error bodies never escape", async () => {
  let exports = 0;
  await assert.rejects(authorizeAccount(options, async (url) => {
    if (url.endsWith('/browser-proxy')) return Response.json({ ...proxy, password: String(exports++) });
    if (url.endsWith('/prepare')) return Response.json({ ticket: 'a'.repeat(64) });
    if (url.endsWith('/authorize')) return Response.json({ flow: 'browser_oauth', authorization_url: 'https://auth.openai.com/authorize?state=1234567890abcdef&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback' });
    assert.fail('changed ISP must stop');
  }), { code: 'changed' });
  await assert.rejects(authorizeAccount(options, async () => new Response('private-token', { status: 401 })), { message: 'http_401' });
});
