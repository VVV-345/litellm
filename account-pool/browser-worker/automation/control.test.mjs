// 验证接管先停脚本再放行输入，以及超时、重复点击和人工等待边界。
import assert from "node:assert/strict";
import test from "node:test";
import { BrowserControl, createControlServer } from "./control.mjs";

const fixture = (login, extra = {}) => {
  const inputs = [];
  const controller = new BrowserControl({
    login, enableInput: async (enabled) => inputs.push(enabled),
    verifyAccount: async () => true, close: async () => {},
    totalDeadline: Date.now() + 600_000, humanMs: 300_000, ...extra,
  });
  return { controller, inputs };
};

test("takeover acknowledges only after automation stops and repeated start cannot submit again", async () => {
  let running = false;
  let launches = 0;
  const { controller, inputs } = fixture(async (signal) => {
    running = true;
    launches += 1;
    await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    running = false;
    return { status: "stopped" };
  });
  await controller.command("start");
  const duplicate = await controller.command("start");
  assert.equal(duplicate.status, "running");
  const paused = await controller.command("takeover");
  assert.equal(paused.status, "manual");
  assert.equal(running, false);
  assert.equal(launches, 1);
  assert.deepEqual(inputs, [false, true]);
  await controller.command("cancel");
  assert.equal(inputs.at(-1), false);
});

test("returning control with an unverified account remains view-only and cannot resume", async () => {
  const { controller, inputs } = fixture(async () => ({ status: "verification_required" }), {
    verifyAccount: async () => false,
  });
  await controller.command("start");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal((await controller.command("status")).status, "waiting");
  await controller.command("takeover");
  const returned = await controller.command("resume");
  assert.equal(returned.status, "waiting");
  assert.equal(returned.reason, "account_verification_required");
  assert.equal(inputs.at(-1), false);
  await controller.command("cancel");
});

test("human timeout is inside the total deadline and cannot be extended by repeated takeover", async () => {
  let now = 1000;
  let closed = false;
  const { controller } = fixture(async () => ({ status: "verification_required" }), {
    now: () => now, totalDeadline: 2000, humanMs: 500,
    close: async () => { closed = true; },
  });
  await controller.command("start");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await controller.command("takeover");
  now = 1400;
  await controller.command("takeover");
  now = 1501;
  const expired = await controller.command("status");
  assert.equal(expired.status, "expired");
  assert.equal(closed, true);
});

test("the job channel authenticates every command, accepts credentials once and never returns them", async () => {
  let starts = 0;
  const server = createControlServer({
    token: "job-token-not-manager-token-12345678",
    createController: async (credentials) => {
      assert.equal(credentials, "private-credential-input");
      starts += 1;
      return fixture(async () => ({ status: "verification_required" })).controller;
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/control`;
  const send = (body, authenticated = true) => fetch(url, {
    method: "POST", body: JSON.stringify(body),
    headers: { ...(authenticated ? { Authorization: "Bearer job-token-not-manager-token-12345678" } : {}) },
  });
  try {
    assert.equal((await send({ action: "start", credentials: "private-credential-input" }, false)).status, 401);
    assert.equal(starts, 0);
    const responses = await Promise.all([send({ action: "start", credentials: "private-credential-input" }), send({ action: "start", credentials: "private-credential-input" })]);
    for (const response of responses) {
      assert.equal(response.status, 200);
      const body = await response.text();
      assert.equal(body.includes("private-credential-input"), false);
      assert.equal(body.includes("job-token"), false);
    }
    assert.equal(starts, 1);
    assert.equal((await send({ action: "execute", script: "not-allowed" })).status, 400);
    assert.equal((await send({ action: "status", credentials: "not-allowed" })).status, 400);
    await send({ action: "cancel" });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
