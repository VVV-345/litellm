// 验证 PR #4 受信授权地址边界，禁止重定向和重复 state 混淆。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { promisify } from "node:util";
import { runInNewContext } from "node:vm";
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

function driverFixture(failure) {
  const events = [];
  const messages = [];
  const entered = Promise.withResolvers();
  const release = Promise.withResolvers();
  let ready = false;
  let closed = false;
  let connected = true;
  const process = {
    env: { CHROME_PROXY: "http://egress-relay:8080", OAUTH_AUTHORIZATION_URL: valid().href },
    once: () => {},
    stderr: { write: (line) => {
      messages.push(line);
      if (line.startsWith("oauth_browser_startup ")) {
        events.push({ ...JSON.parse(line.slice("oauth_browser_startup ".length)), ready, closed });
      }
    } },
  };
  const browser = {
    newContext: async () => ({ newPage: async () => ({ goto: async () => {
      if (failure === "navigation") throw new Error("DO_NOT_LOG");
    } }) }),
    isConnected: () => connected,
    close: async () => {
      closed = true;
      if (failure === "close") throw new Error("DO_NOT_LOG");
    },
  };
  // 执行真实入口，只替换浏览器、进程、文件和时钟边界，不启动外部程序。
  const source = readFileSync(new URL("./browser.mjs", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
  const done = runInNewContext(source, {
    ...startup, promisify, process, AbortController, URL,
    execFile: (_file, _args, _options, callback) => callback(null, "", ""),
    execFileSync: () => "{}",
    firefox: { launch: async () => {
      if (failure === "launch") throw new Error("DO_NOT_LOG");
      return browser;
    } },
    writeFileSync: () => {
      if (failure === "ready_file") throw new Error("DO_NOT_LOG");
      ready = true;
    },
    rmSync: () => { ready = false; },
    setTimeout: async () => { entered.resolve(); await release.promise; connected = false; },
    runStartupStage: (stage, operation) => startup.runStartupStage(stage, operation, {
      log: (event) => process.stderr.write(`oauth_browser_startup ${JSON.stringify(event)}\n`),
    }),
    openAuthorizationPage: (page, authorization, seconds) => startup.openAuthorizationPage(page, authorization, seconds, {
      log: (event) => process.stderr.write(`oauth_browser_startup ${JSON.stringify(event)}\n`),
    }),
  }, { filename: "browser.mjs" });
  return { events, messages, process, done, entered: entered.promise, release: release.resolve, closed: () => closed };
}

test("driver readiness is emitted once after the ready file, while the session is active", { timeout: 2000 }, async () => {
  const driver = driverFixture();
  try {
    await driver.entered;
    const ready = driver.events.filter((event) => event.stage === "driver" && event.status === "ready");
    assert.equal(ready.length, 1);
    assert.equal(ready[0].ready, true);
    assert.equal(ready[0].closed, false);
  } finally {
    driver.release();
    await driver.done;
  }
  assert.equal(driver.events.filter((event) => event.stage === "driver" && event.status === "ready").length, 1);
  assert.equal(driver.closed(), true);
});

for (const failure of ["launch", "navigation", "ready_file"]) {
  test(`driver readiness is not emitted when ${failure} fails`, { timeout: 2000 }, async () => {
    const driver = driverFixture(failure);
    await driver.done;
    assert.deepEqual(driver.events.filter((event) => event.stage === "driver").map(({ status }) => status), ["started", "failed"]);
    assert.equal(driver.closed(), failure !== "launch");
    assert.equal(driver.process.exitCode, 1);
    assert.equal(driver.messages.join("").includes("DO_NOT_LOG"), false);
  });
}

test("driver readiness is not repeated or reclassified by a later cleanup failure", { timeout: 2000 }, async () => {
  const driver = driverFixture("close");
  await driver.entered;
  driver.release();
  await driver.done;
  const events = driver.events.filter((event) => event.stage === "driver");
  assert.deepEqual(events.map(({ status }) => status), ["started", "ready"]);
  assert.equal(events[1].ready, true);
  assert.equal(events[1].closed, false);
  assert.equal(driver.process.exitCode, 1);
  assert.equal(driver.messages.join("").includes("DO_NOT_LOG"), false);
});
