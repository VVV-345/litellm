// 验证外部脚本包保留相对依赖，拒绝越界路径，诊断不回显 URL。
import assert from "node:assert/strict";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { loadLoginScript, observeLoginPage, toPageDiagnostic } from "./login_script.mjs";

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
  const observed = observeLoginPage(page);
  assert.deepEqual(observed.current(), { step: "login", reason: "navigation_http_error", http_status: 403 });
  assert.deepEqual(await observed.inspect(), {
    origin: "openai_auth", route: "other", step: "login", httpStatus: 403,
    reason: "browser_security_challenge", inputs: 0, challenge: true,
  });
});

test("classifies localized manual checks without forwarding page text or input values", async () => {
  const trusted = "https://auth.openai.com";
  const cases = [
    ["ja-JP", "", "メールアドレスを確認してください", "", trusted, "/step", 200, "email_verification", "email_verification"],
    ["ja-JP", "pt-BR", "Verifique seu e-mail", "", trusted, "/step", 200, "email_verification", "email_verification"],
    ["pt-BR", "", "", "Verifique se você é humano", trusted, "/step", 403, "login", "browser_security_challenge"],
    ["pt-BR", "ja", "", "人間であることを確認", trusted, "/step", 403, "login", "browser_security_challenge"],
    ["ja-JP", "pt-BR", "Confirme que você é humano", "", trusted, "/pt-BR/log-in/password", 403, "password", "browser_security_challenge"],
    ["ja-JP", "en", "Enter your code", "", trusted, "/step", 200, "login", "unrecognized_route"],
    ["ja-JP", "", "メールアドレスを確認してください", "", trusted, "/verify-email", 429, "email_verification", "navigation_http_error"],
    ["pt-BR", "", "Verifique seu e-mail", "", trusted, "/pt-BR/mfa-challenge/totp", 200, "authenticator", "form_or_transition_not_confirmed"],
    ["ru-RU", "ru-RU", "", "", trusted, "/ru-RU/log-in/password", 200, "password", "form_or_transition_not_confirmed"],
    ["tr-TR", "tr-TR", "", "", trusted, "/tr-TR/mfa-challenge/totp", 200, "authenticator", "form_or_transition_not_confirmed"],
    ["uk-UA", "uk-UA", "", "", trusted, "/uk-UA/log-in", 200, "email", "form_or_transition_not_confirmed"],
    ["ru-RU", "", "", "", trusted, "/api/log-in", 200, "login", "unrecognized_route"],
    ["ja-JP", "", "メールアドレスを確認してください", "Just a moment", "https://other.test", "/verify-email", 200, "login", "unexpected_origin"],
  ];
  for (const [profile, lang, heading, title, origin, path, code, step, reason] of cases) {
    const url = origin + path + "?state=private-fixture";
    const document = {
      documentElement: { lang }, title,
      querySelectorAll: () => ({ length: 1 }),
      querySelector: selector => selector.includes("h1") ? { textContent: heading }
        : selector.includes("autocomplete") ? {} : null,
    };
    const page = {
      url: () => url, mainFrame: () => 1,
      on: (_, fn) => fn({ url: () => url, status: () => code, request: () => ({ isNavigationRequest: () => true, frame: () => 1 }) }),
      evaluate: (fn, arg) => Promise.resolve(runInNewContext(`(${fn})(arg)`, { document, arg })),
    };
    const diagnostic = toPageDiagnostic(await observeLoginPage(page, profile).inspect());
    assert.deepEqual(diagnostic, { step, reason, http_status: code }, profile + " / " + path);
  }
});


test("the current built-in login can be uploaded as one self-contained file", async () => {
  const { readFile } = await import("node:fs/promises");
  const content = await readFile(new URL("./login.mjs", import.meta.url));
  const payload = JSON.stringify({ entrypoint: "login.mjs", files: [{ path: "login.mjs", data: content.toString("base64") }] });
  const loaded = await loadLoginScript(payload);
  try {
    assert.equal(typeof loaded.automateLogin, "function");
    assert.equal(typeof loaded.verifyResumableAccount, "function");
  } finally {
    await loaded.cleanup();
  }
});
