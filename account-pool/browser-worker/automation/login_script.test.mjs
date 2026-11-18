// 验证外部脚本包保留相对依赖，拒绝越界路径，诊断不回显 URL。
import assert from "node:assert/strict";
import { test } from "node:test";
import { loadLoginScript, observeLoginPage } from "./login_script.mjs";

const file = (path, source) => ({ path, data: Buffer.from(source).toString("base64") });

test("loads a chosen entry with added helper and data files", async () => {
  const source = "import {text} from './helper.mjs'; import {readFile} from 'node:fs/promises';" +
    "export async function automateLogin(){return text+await readFile(new URL('../extra.json',import.meta.url),'utf8')};" +
    "export function verifyResumableAccount(){return true}";
  const files = [file("a/run.mjs", source), file("a/helper.mjs", "export const text='extra:';"), file("extra.json", "{}")];
  const loaded = await loadLoginScript(JSON.stringify({ entrypoint: "a/run.mjs", files }));
  try {
    assert.equal(await loaded.automateLogin(), "extra:{}");
    assert.match(loaded.digest, /^[a-f0-9]{64}$/);
  } finally {
    await loaded.cleanup();
  }
});

test("rejects traversal and a module without required exports", async () => {
  const entry = file("run.mjs", "export const foo=2");
  const manifest = { entrypoint: entry.path, files: [entry] };
  await assert.rejects(loadLoginScript(JSON.stringify({ ...manifest, files: [entry, file("../escape", "x")] })), /login_script_path/);
  await assert.rejects(loadLoginScript(JSON.stringify(manifest)), /login_script_exports/);
  assert.equal((await loadLoginScript()).digest, null);
});

test("classifies an OAuth security page without exposing location tokens", async () => {
  const url = "https://auth.openai.com/new-path/private?state=fixture-only";
  const mainFrame = {};
  const page = {
    mainFrame: () => mainFrame,
    on: (_, observe) => {
      observe({
        request: () => ({ isNavigationRequest: () => true, frame: () => mainFrame }),
        url: () => url, status: () => 403,
      });
      observe({
        request: () => ({ isNavigationRequest: () => true, frame: () => { throw new Error("frame not available"); } }),
        url: () => url, status: () => 200,
      });
    },
    url: () => url,
    evaluate: async () => ({ inputs: 0, challenge: true }),
  };
  const cause = await observeLoginPage(page)();
  assert.deepEqual(cause, { origin: "openai_auth", route: "other", httpStatus: 403, reason: "browser_security_challenge", inputs: 0, challenge: true });
});
