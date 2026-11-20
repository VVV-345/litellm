// 验证 PR #4 受信授权地址边界，禁止重定向和重复 state 混淆。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { promisify } from "node:util";
import { runInNewContext } from "node:vm";
import { validateAuthorization } from "./core.mjs";
import * as startup from "./core.mjs";
import { BrowserControl } from "./control.mjs";
import { AUTOMATION_LOGIN_TIMEOUT_MS, parseCredential } from "./login.mjs";
import { observeLoginPage, toPageDiagnostic, loginFailureDiagnostic } from "./login_script.mjs";

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

test("slow authorization navigation waits only for commit so a challenge page cannot block startup", async () => {
  const events = [];
  let calls = 0;
  let now = 0;
  const page = { goto: async (url, options) => {
    calls += 1;
    assert.equal(url, valid().href);
    assert.equal(options.waitUntil, "commit");
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

function driverFixture(failure, automatic = false, scriptResult = { status: "stopped" }) {
  const events = [];
  const messages = [];
  const budgets = [];
  let clock = Date.now();
  let createController;
  const entered = Promise.withResolvers();
  const release = Promise.withResolvers();
  let ready = false;
  let closed = false;
  let connected = true;
  const process = {
    env: { CHROME_PROXY: "http://egress-relay:8080", OAUTH_AUTHORIZATION_URL: valid().href,
      ...(automatic ? { OAUTH_AUTOMATION: "true", OAUTH_FINGERPRINT_PROFILE: '{"locale":"ja-JP"}',
        OAUTH_BROWSER_EXPIRES_AT: new Date(clock + 100_000).toISOString() } : {}),
    },
    once: () => {},
    stderr: { write: (line) => {
      messages.push(line);
      if (line.startsWith("oauth_browser_startup ")) {
        events.push({ ...JSON.parse(line.slice("oauth_browser_startup ".length)), ready, closed });
      }
    } },
  };
  const browser = {
    newContext: async () => ({ close: async () => {}, newPage: async () => ({
      url: () => "https://auth.openai.com/log-in", on: () => {},
      evaluate: async () => ({ inputs: 1, challenge: false, email: false }),
      getByLabel: () => ({ count: async () => 0 }), locator: () => ({ count: async () => 0 }),
      goto: async () => {
        if (failure === "navigation") throw new Error("DO_NOT_LOG");
      },
    }) }),
    isConnected: () => connected,
    close: async () => {
      closed = true;
      if (failure === "close") throw new Error("DO_NOT_LOG");
    },
  };
  // 执行真实入口，只替换浏览器、进程、文件和时钟边界，不启动外部程序。
  const source = readFileSync(new URL("./browser.mjs", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
  const done = runInNewContext(source, {
    ...startup, AUTOMATION_LOGIN_TIMEOUT_MS, promisify, process, AbortController, URL, BrowserControl, parseCredential, observeLoginPage, toPageDiagnostic, loginFailureDiagnostic,
    Date: class extends Date { static now() { return clock; } },
    loadLoginScript: async () => ({ digest: null, cleanup: async () => {},
      automateLogin: async (_page, _credential, options) => {
        budgets.push(options.timeoutMs); return scriptResult;
      },
      verifyResumableAccount: async () => true,
    }),
    createControlServer: (options) => {
      createController = options.createController;
      return { once: () => {}, off: () => {}, listen: (_port, _host, ready) => ready(), close: (done) => done() };
    },
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
  return { events, messages, process, done, entered: entered.promise, release: release.resolve, closed: () => closed,
    budgets, advance: (milliseconds) => { clock += milliseconds; }, start: (text) => createController(text),
  };
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


test("automatic driver forwards remaining session time on start and resume", { timeout: 2000 }, async () => {
  const driver = driverFixture(undefined, true);
  try {
    await driver.entered;
    const control = await driver.start("fixture@example.test----fixture-password----GEZDGNBVGY3TQOJQ");
    await control.command("start");
    await control.command("takeover");
    driver.advance(30_000);
    await control.command("resume");
    await control.command("takeover");
    assert.deepEqual(driver.budgets, [100_000, 70_000]);
  } finally {
    driver.release();
    await driver.done;
  }
});


for (const [reason, expected] of [
  ["form_fill_timeout", "form_fill_timeout"], ["form_fill_failed", "form_fill_failed"],
  ["form_submit_timeout", "form_submit_timeout"], ["form_submit_failed", "form_submit_failed"],
  ["PRIVATE-input-and-URL", "form_or_transition_not_confirmed"],
]) {
  test(`worker returns and logs only the approved ${expected} diagnostic`, { timeout: 2000 }, async () => {
    const driver = driverFixture(undefined, true, { status: "verification_required", diagnostic: {
      step: "email", reason, http_status: "PRIVATE", message: "PRIVATE", url: "PRIVATE",
    } });
    try {
      await driver.entered;
      const control = await driver.start("fixture@example.test----fixture-password----GEZDGNBVGY3TQOJQ");
      await control.command("start");
      await control.pending;
      assert.equal(control.view().status, "waiting");
      assert.equal(control.diagnosticView().reason, expected);
      const logged = driver.messages.filter(line => line.startsWith("OAuth form state: ")).join("");
      assert.ok(logged.includes(expected));
      assert.equal(logged.includes("PRIVATE"), false);
      assert.deepEqual(Object.keys(control.diagnosticView()).sort(), ["http_status", "reason", "step"]);
    } finally {
      driver.release();
      await driver.done;
    }
  });
}
