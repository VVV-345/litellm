// 验证本机浏览器的代理去重、环境隔离及过期操作，不启动真实账号登录。
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EventEmitter } from "node:events";
import { CamoufoxSessions, upstreamUrl } from "./core.mjs";

const proxy = {
  proxy_type: "socks5",
  host: "isp.example",
  port: 1080,
  username: "alice@example",
  password: "p:/@#%",
};
const card = "e5304d0c-462d-46c5-a738-7c3fc31de8ec";
const origin = "http://127.0.0.1:4001";
const url =
  "https://auth.openai.com/oauth/authorize?state=1234567890abcdef&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback";
function fixture(t, options = {}) {
  const root = mkdtempSync(join(tmpdir(), "camoufox-test-"));
  const launches = [];
  const pages = [];
  const contexts = [];
  const launch = async (directory, selected) => {
    launches.push({ directory, selected });
    const context = new EventEmitter();
    context.newPage = async () => {
      const page = {
        urls: [],
        goto: async (target) => {
          page.urls.push(target);
        },
        bringToFront: async () => {},
        route: async (_pattern, handler) => {
          page.callbackHandler = handler;
        },
      };
      pages.push(page);
      return page;
    };
    context.route = async () => {};
    context.close = async () => {
      context.emit("close");
    };
    contexts.push(context);
    return context;
  };
  const sessions = new CamoufoxSessions({
    root,
    key: "test-key-long-enough",
    launch,
    ...options,
  });
  t.after(async () => {
    await sessions.close();
    rmSync(root, { recursive: true, force: true });
  });
  return { sessions, root, launches, pages, contexts };
}
test("matches an ISP, isolates cards and never persists proxy credentials", async (t) => {
  const { sessions, root, launches } = fixture(t);
  const first = await sessions.prepare(origin, { card_id: card, proxy });
  const repeated = await sessions.prepare(origin, {
    card_id: card,
    proxy: { ...proxy, host: "ISP.EXAMPLE" },
  });
  assert.equal(first.imported, true);
  assert.equal(repeated.imported, false);
  assert.equal(first.proxy_id, repeated.proxy_id);
  assert.equal(launches.length, 1);
  await sessions.prepare(origin, {
    card_id: "c7c22a35-4435-4b68-89ba-42cb0603aedd",
    proxy,
  });
  assert.equal(launches.length, 2);
  assert.notEqual(launches[0].directory, launches[1].directory);
  const saved = readFileSync(join(root, "registry.json"), "utf8");
  assert.equal(saved.includes(proxy.password), false);
  assert.equal(saved.includes(proxy.username), false);
});
test("refuses changing the proxy of a live environment but permits it after closing", async (t) => {
  const { sessions, contexts } = fixture(t);
  await sessions.prepare(origin, { card_id: card, proxy });
  await assert.rejects(
    sessions.prepare(origin, {
      card_id: card,
      proxy: { ...proxy, password: "new" },
    }),
    { code: "running" },
  );
  await contexts[0].close();
  assert.equal(
    (
      await sessions.prepare(origin, {
        card_id: card,
        proxy: { ...proxy, password: "new" },
      })
    ).imported,
    true,
  );
});
test("opens only the approved OAuth host and binds tickets to the dashboard origin", async (t) => {
  const { sessions, pages } = fixture(t);
  const prepared = await sessions.prepare(origin, { card_id: card, proxy });
  await assert.rejects(
    sessions.open("https://evil.example", { ticket: prepared.ticket, url }),
    { code: "expired" },
  );
  await assert.rejects(
    sessions.open(origin, {
      ticket: prepared.ticket,
      url: "https://evil.example/oauth?state=secret",
    }),
    { code: "url" },
  );
  await sessions.open(origin, { ticket: prepared.ticket, url });
  await sessions.open(origin, { ticket: prepared.ticket, url });
  assert.equal(pages.length, 1);
  assert.deepEqual(pages[0].urls, [url]);
});
test("rejects a ticket after its environment closes", async (t) => {
  const { sessions, contexts } = fixture(t);
  const prepared = await sessions.prepare(origin, { card_id: card, proxy });
  await contexts[0].close();
  await assert.rejects(
    sessions.open(origin, { ticket: prepared.ticket, url }),
    { code: "expired" },
  );
});
test("encodes credentials and asks SOCKS to resolve the destination remotely", () => {
  assert.equal(
    upstreamUrl(proxy),
    "socks5h://alice%40example:p%3A%2F%40%23%25@isp.example:1080",
  );
  assert.equal(
    upstreamUrl({ ...proxy, proxy_type: "https", host: "2001:db8::1" }),
    "https://alice%40example:p%3A%2F%40%23%25@[2001:db8::1]:1080",
  );
});

test("shutdown waits for a launching browser and closes it before returning", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "camoufox-shutdown-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  let release;
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  const context = new EventEmitter();
  let closed = false;
  context.close = async () => {
    closed = true;
    context.emit("close");
  };
  const sessions = new CamoufoxSessions({
    root,
    key: "key",
    launch: () => pending,
  });
  const preparing = sessions.prepare(origin, { card_id: card, proxy });
  const rejected = assert.rejects(preparing, { code: "busy" });
  const closing = sessions.close();
  release(context);
  await closing;
  await rejected;
  assert.equal(closed, true);
  await assert.rejects(sessions.prepare(origin, { card_id: card, proxy }), {
    code: "busy",
  });
});

test("callback forwarding preserves the query only for the active OAuth state and callback path", async (t) => {
  const { sessions, pages } = fixture(t, { callbackPort: 1456 });
  const prepared = await sessions.prepare(origin, { card_id: card, proxy });
  await sessions.open(origin, { ticket: prepared.ticket, url });
  let forwarded;
  let aborted = false;
  const route = (target) => ({
    request: () => ({ url: () => target }),
    abort: async () => {
      aborted = true;
    },
    fulfill: async (value) => {
      forwarded = value;
    },
  });
  await pages[0].callbackHandler(
    route("http://localhost:1455/auth/callback?state=wrong&code=test"),
  );
  assert.equal(aborted, true);
  assert.equal(forwarded, undefined);
  await pages[0].callbackHandler(
    route(
      "http://localhost:1455/auth/callback?state=1234567890abcdef&code=test",
    ),
  );
  assert.equal(
    forwarded.headers.Location,
    "http://127.0.0.1:1456/auth/callback?state=1234567890abcdef&code=test",
  );
  assert.equal(forwarded.headers["Cache-Control"], "no-store");
});

test("rejects literal IPv6 SOCKS endpoints unsupported by the bridge instead of silently failing", () => {
  assert.throws(() => upstreamUrl({ ...proxy, host: "::1" }), {
    code: "proxy",
  });
});

test("login requires the current opened ticket, consumes it once and aborts on shutdown", async (t) => {
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  const { sessions, root } = fixture(t, { login: async (_page, account, { signal }) => {
    assert.equal(account.email, 'test@example.test');
    started();
    await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
    return { status: 'stopped' };
  } });
  const prepared = await sessions.prepare(origin, { card_id: card, proxy });
  const input = { ticket: prepared.ticket, credentials: 'test@example.test----private-password----GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ' };
  await assert.rejects(sessions.login(origin, input), { code: 'expired' });
  await sessions.open(origin, { ticket: prepared.ticket, url });
  await assert.rejects(sessions.login('https://evil.example', input), { code: 'expired' });
  const login = sessions.login(origin, input);
  await ready;
  await assert.rejects(sessions.prepare(origin, { card_id: card, proxy }), { code: 'busy' });
  await assert.rejects(sessions.login(origin, input), { code: 'busy' });
  await sessions.close();
  assert.deepEqual(await login, { status: 'stopped' });
  assert.equal(readFileSync(join(root, 'registry.json'), 'utf8').includes('private-password'), false);
});

test("a newer OAuth page invalidates the previous login ticket and requests cannot be replayed", async (t) => {
  const loginOptions = [];
  const { sessions } = fixture(t, { login: async (_page, _account, options) => {
    loginOptions.push(options);
    return { status: 'callback_received' };
  } });
  const one = await sessions.prepare(origin, { card_id: card, proxy });
  const two = await sessions.prepare(origin, { card_id: card, proxy });
  await sessions.open(origin, { ticket: one.ticket, url });
  await sessions.open(origin, { ticket: two.ticket, url });
  const input = { credentials: 'test@example.test----pw----GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ' };
  await assert.rejects(sessions.login(origin, { ...input, ticket: one.ticket }), { code: 'expired' });
  assert.equal((await sessions.login(origin, { ...input, ticket: two.ticket })).status, 'callback_received');
  assert.equal(loginOptions[0].allowConsent, true);
  await assert.rejects(sessions.login(origin, { ...input, ticket: two.ticket }), { code: 'expired' });
});
