// 验证官网账号证明、显式提交、防重放、只读核实和可替换脚本契约。
import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout } from "node:timers/promises";
import { createRebindingControl, webIdentity } from "./rebinding.mjs";
import { loadLoginScript } from "./login_script.mjs";
import { createControlServer } from "./control.mjs";

const old = { subject: "user-1", email: "old@example.test" };
const target = { email: "new@example.test", verify_only: false, subject: null };
function fixture(script, extra = {}) {
  const session = { value: old };
  const page = { url: () => "https://chatgpt.com/", evaluate: async () => session.value };
  const control = createRebindingControl({ page, account: { email: old.email, password: "fixture-pwd" },
    input: async () => {}, close: async () => {}, script, locale: "ja-JP", deadline: Date.now()+30000,
    humanMs: 10000, target: { ...target, ...extra } });
  return { control, session, page };
}
async function waitFor(control, predicate) {
  for (let i=0;i<100;i++) {
    const view = await control.command("status");
    if (predicate(view)) return view;
    await setTimeout(1);
  }
  assert.fail("control did not reach expected state");
}

test("checkpoint waits for Manager commit, accepts code, then proves same user with new email", async () => {
  let changed = 0;
  let session;
  const script = { rebindEmail: async (page, account, options) => {
    assert.equal(options.targetEmail, target.email);
    if (!await options.checkpoint(old)) return { status: "stopped" };
    changed++;
    assert.equal(await options.receiveCode(), "123456");
    session.value = { ...old, email: target.email };
    return { status: "done" };
  } };
  const f = fixture(script); session = f.session;
  await f.control.command("start");
  await waitFor(f.control, v => v.step === "awaiting_commit");
  assert.equal(changed, 0);
  await f.control.command("commit");
  await waitFor(f.control, v => v.step === "waiting_code");
  await assert.rejects(f.control.command("code", { code: "invalid" }), /unexpected_code/);
  await f.control.command("code", { code: "123456" });
  const done = await waitFor(f.control, v => v.status === "callback_received");
  assert.equal(done.subject, old.subject); assert.equal(done.source_email, old.email);
  await f.control.command("resume"); assert.equal(changed, 1);
});

test("plugin cannot fabricate the logged-in identity or claim success without observed email", async () => {
  let allowed;
  const f = fixture({ rebindEmail: async (_, __, o) => {
    allowed = await o.checkpoint({ ...old, subject: "different-user" });
    return { status: "done" };
  } });
  await f.control.command("start");
  const result = await waitFor(f.control, v => v.status === "waiting");
  assert.equal(allowed, false); assert.notEqual(result.step, "changed");
});

test("takeover after commit and resume never calls the email-changing script twice", async () => {
  let invocations = 0;
  const f = fixture({ rebindEmail: async (_, __, o) => {
    invocations++;
    await o.checkpoint(old);
    await o.receiveCode();
    return { status: "verification_required" };
  } });
  await f.control.command("start");
  await waitFor(f.control, v => v.step === "awaiting_commit");
  await f.control.command("commit");
  await waitFor(f.control, v => v.step === "waiting_code");
  await f.control.command("takeover");
  await f.control.command("resume");
  await waitFor(f.control, v => v.status === "waiting");
  assert.equal(invocations, 1);
  f.session.value = { ...old, email: target.email };
  await f.control.command("resume");
  await waitFor(f.control, v => v.status === "callback_received");
  assert.equal(invocations, 1);
});

test("verify-only restart never executes uploaded email-changing script and rejects another subject", async () => {
  for (const subject of [old.subject, "other-user"]) {
    const f = fixture({ rebindEmail: () => assert.fail("verify-only must not mutate email") },
      { verify_only: true, subject: old.subject });
    f.session.value = { subject, email: target.email };
    await f.control.command("start");
    const result = await waitFor(f.control, v => ["waiting", "callback_received"].includes(v.status));
    assert.equal(result.status, subject === old.subject ? "callback_received" : "waiting");
  }
});

test("only trusted web origin can supply account proof", async () => {
  let evaluated = false;
  assert.equal(await webIdentity({ url: () => "https://evil.test/", evaluate: () => { evaluated=true; } }), null);
  assert.equal(evaluated, false);
});

test("binding script accepts helper files but requires its own export", async () => {
  const file = (path, text) => ({ path, data: Buffer.from(text).toString("base64") });
  const bundle = JSON.stringify({ entrypoint: "rebind.mjs", files: [
    file("rebind.mjs", "import {value} from './added.mjs'; export async function rebindEmail(){return value}"),
    file("added.mjs", "export const value='verification_required'"), file("data.json", "{}") ] });
  const script = await loadLoginScript(bundle, { rebindEmail: () => {} });
  try { assert.equal(await script.rebindEmail(), "verification_required"); }
  finally { await script.cleanup(); }
  await assert.rejects(loadLoginScript(JSON.stringify({ entrypoint: "wrong.mjs", files: [
    file("wrong.mjs", "export async function automateLogin(){}; export function verifyResumableAccount(){}")
  ] }), { rebindEmail: () => {} }), /login_script_exports/);
});

test("old OAuth worker purpose refuses binding attestation before controller receives credentials", async () => {
  let started = 0;
  const token = "fixture-only-token-".repeat(3);
  const server = createControlServer({ token, createController: () => { started++; } });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/control`, { method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ action: "script_check", purpose: "email_rebind", sha256: null }) });
    assert.equal(response.status, 409); assert.equal(started, 0);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
