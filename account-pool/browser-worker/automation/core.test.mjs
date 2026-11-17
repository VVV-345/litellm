// 验证 PR #4 受信授权地址边界，禁止重定向和重复 state 混淆。
import assert from "node:assert/strict";
import test from "node:test";
import { validateAuthorization } from "./core.mjs";
import * as startup from "./core.mjs";

const valid = () => {
  const url = new URL("https://auth.openai.com/oauth/authorize");
  url.searchParams.set("state", "one-time-state-0123456789");
  url.searchParams.set("redirect_uri", "http://localhost:1455/auth/callback");
  return url;
};

test("server-generated Codex authorization retains exact loopback callback and state", () => {
  const result = validateAuthorization(valid().href);
  assert.equal(result.callback.href, "http://localhost:1455/auth/callback");
  assert.equal(result.url.searchParams.get("state"), "one-time-state-0123456789");
});

test("untrusted authorization cannot receive credentials", () => {
  for (const change of [
    (url) => { url.hostname = "auth.openai.com.example.org"; },
    (url) => { url.username = "someone"; },
    (url) => { url.hash = "#state"; },
    (url) => { url.searchParams.append("state", "another-state-0123456789"); },
    (url) => { url.searchParams.append("redirect_uri", "http://localhost:1456/auth/callback"); },
    (url) => { url.searchParams.set("redirect_uri", "https://example.org/auth/callback"); },
    (url) => { url.searchParams.set("redirect_uri", "http://localhost:1455/auth/callback?state=other"); },
    (url) => { url.searchParams.set("state", "x".repeat(513)); },
  ]) {
    const url = valid();
    change(url);
    assert.throws(() => validateAuthorization(url.href), /url/);
  }
});

test("slow authorization navigation uses the shared 60 second budget without retries", async () => {
  const events = [];
  let calls = 0;
  let now = 0;
  const page = { goto: async (url, options) => {
    calls += 1;
    assert.equal(url, valid().href);
    assert.equal(options.waitUntil, "domcontentloaded");
    assert.equal(options.timeout, 60_000);
    now = 50_000;
  } };
  await startup.openAuthorizationPage(page, valid().href, "60", { now: () => now, log: (event) => events.push(event) });
  assert.equal(calls, 1);
  assert.deepEqual(events.map(({ status, elapsed_ms }) => [status, elapsed_ms]), [["started", 0], ["ready", 50_000]]);
});

test("startup errors report only safe stage, category and elapsed time", async () => {
  for (const [name, message, reason] of [
    ["TimeoutError", "https://auth.example/?state=DO_NOT_LOG password=DO_NOT_LOG", "timeout"],
    ["Error", "net::ERR_PROXY_CONNECTION_FAILED DO_NOT_LOG", "proxy_error"],
    ["Error", "SSL_ERROR DO_NOT_LOG", "tls_error"],
    ["Error", "password DO_NOT_LOG", "process_error"],
  ]) {
    const events = [];
    const error = Object.assign(new Error(message), { name });
    await assert.rejects(startup.runStartupStage("page_navigation", async () => { throw error; }, {
      now: () => 10, log: (event) => events.push(event),
    }), (caught) => caught === error);
    assert.equal(events.at(-1).reason, reason);
    assert.equal(events.at(-1).stage, "page_navigation");
    assert.equal(events.at(-1).status, "failed");
    assert.equal(JSON.stringify(events).includes("DO_NOT_LOG"), false);
  }
});

test("navigation timeout propagates once without retrying or reporting readiness", async () => {
  const events = [];
  let calls = 0;
  let now = 0;
  const page = { goto: async (_url, { timeout }) => {
    calls += 1;
    now = timeout;
    throw Object.assign(new Error("state=DO_NOT_LOG"), { name: "TimeoutError" });
  } };
  await assert.rejects(startup.openAuthorizationPage(page, valid().href, "60", {
    now: () => now, log: (event) => events.push(event),
  }), { name: "TimeoutError" });
  assert.equal(calls, 1);
  assert.deepEqual(events.map(({ status }) => status), ["started", "failed"]);
  assert.equal(events.at(-1).elapsed_ms, 60_000);
  assert.equal(JSON.stringify(events).includes("DO_NOT_LOG"), false);
});
