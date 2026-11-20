// 只用合成资料和离线页面，在真实浏览器核对多语言登录、账号边界和回调；不连接真实账号。
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { automateLogin } from "../automation/login.mjs";

const [modulePath, executablePath, engine = "chromium"] = process.argv.slice(2);
if (!modulePath || !executablePath || !["chromium", "firefox"].includes(engine)) {
  throw new Error("usage: node oauth_login_browser.mjs PLAYWRIGHT_MODULE BROWSER_EXECUTABLE [chromium|firefox]");
}
const sdk = await import(pathToFileURL(modulePath).href);
const browser = await sdk[engine].launch({ executablePath, headless: true, proxy: { server: "http://127.0.0.1:1" } });
const origin = "https://auth.openai.com";
const callback = "http://127.0.0.1:1456/auth/callback?state=fixture";
const credential = { email: "fixture+o2@example.test", password: "fixture^password&only", totpSecret: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" };
const languages = {
  "en-US": ["Email address", "Password", "Check your authenticator app", "One-time code", "Continue"],
  "ja-JP": ["メールアドレス", "パスワード", "認証アプリを確認してください", "ワンタイムコード", "続行"],
  "pt-BR": ["Endereço de e-mail", "Senha", "Confira seu aplicativo autenticador", "Código de uso único", "Continuar"],
  "ru-RU": ["Адрес электронной почты", "Пароль", "Откройте приложение-аутентификатор", "Одноразовый код", "Продолжить"],
  "tr-TR": ["E-posta adresi", "Şifre", "Kimlik doğrulama uygulamanızı kontrol edin", "Tek kullanımlık kod", "Devam et"],
  "uk-UA": ["Електронна адреса", "Пароль", "Перевірте застосунок-автентифікатор", "Одноразовий код", "Продовжити"],
};
const cases = [
  ...["ru-RU", "tr-TR", "uk-UA"].flatMap(locale => [
    { name: `${locale}-localized-mfa-and-consent`, locale, prefix: `/${locale}`, textOnly: true },
    { name: `${locale}-unbound-account`, locale, prefix: `/${locale}`, negative: "missing" },
  ]),
  { name: "ordinary-login", locale: "en-US", prefix: "" },
  { name: "japanese-prefix-text-account", locale: "ja-JP", prefix: "/ja-JP", textOnly: true },
  { name: "brazil-prefix-text-account", locale: "pt-BR", prefix: "/pt-BR", textOnly: true },
  { name: "conflicting-input", locale: "ja-JP", prefix: "/ja-JP", negative: "mismatch" },
  { name: "missing-account", locale: "ja-JP", prefix: "/ja-JP", negative: "missing" },
  { name: "ambiguous-account", locale: "ja-JP", prefix: "/ja-JP", negative: "ambiguous" },
  { name: "email-code-needs-owner", locale: "ja-JP", prefix: "/ja-JP", negative: "email" },
  { name: "foreign-origin", locale: "ja-JP", prefix: "/ja-JP", negative: "foreign" },
  { name: "wrong-callback-state", locale: "ja-JP", prefix: "/ja-JP", textOnly: true, negative: "state" },
];
try {
  for (const scenario of cases) {
    const context = await browser.newContext({ locale: scenario.locale, serviceWorkers: "block" });
    const actions = [], unexpected = [], pageErrors = [], routeFailures = [];
    let callbacks = 0;
    const [email, password, heading, code, button] = languages[scenario.locale];
    const prefix = scenario.prefix;
    const address = `<p>${credential.email}</p>`;
    const account = scenario.negative === "missing" ? "" : scenario.negative === "ambiguous" ? address + address : address;
    const emailInput = scenario.textOnly || ["missing", "ambiguous"].includes(scenario.negative) ? "" :
      `<label>${email}<input type="email" readonly value="${scenario.negative === "mismatch" ? "other@example.test" : credential.email}"></label>`;
    const finalCallback = (scenario.negative === "state" ? callback.replace("state=fixture", "state=other") : callback) + "&code=fixture";
    const pages = {
      [prefix + "/log-in"]: { field: "email", markup: `<label>${email}<input type="email"></label>`, next: prefix + "/log-in/password", expected: credential.email },
      [prefix + "/log-in/password"]: { field: "password", markup: account + emailInput + `<label>${password}<input type="password"></label>`, next: prefix + "/mfa-challenge/totp", expected: credential.password },
      [prefix + "/mfa-challenge/totp"]: { field: "code", markup: `<h1>${heading}</h1><label>${code}<input autocomplete="one-time-code"></label>`, next: prefix + "/sign-in-with-chatgpt/codex/consent", expected: "287082" },
      [prefix + "/sign-in-with-chatgpt/codex/consent"]: { field: "consent", markup: address, next: finalCallback },
      [prefix + "/verify-email"]: { field: "email-code", markup: '<h1>メールアドレスを確認してください</h1><input autocomplete="one-time-code">' },
    };
    try {
      await context.route("**/*", async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin === origin && url.pathname === "/fixture-submit") {
          const action = request.postDataJSON();
          if (action.correct !== true) {
            routeFailures.push(action.field);
            await route.abort(); return;
          }
          actions.push(action.field);
          await route.fulfill({ status: 204 }); return;
        }
        if (url.origin === "http://127.0.0.1:1456" && url.pathname === "/auth/callback") {
          callbacks += 1;
          await route.fulfill({ contentType: "text/html", body: "<h1>Offline callback</h1>" }); return;
        }
        const step = pages[url.pathname];
        if (!step || (url.origin !== origin && scenario.negative !== "foreign")) {
          unexpected.push(url.origin); await route.abort(); return;
        }
        await route.fulfill({ contentType: "text/html; charset=utf-8", body: `<!doctype html><html lang="${scenario.locale}">
          <meta charset="utf-8"><title>ChatGPTでCodexにサインイン - OpenAI</title><body>${step.markup}
          <button type="button">${button}</button><script>
          document.querySelector('button').onclick = async () => {
            const input = document.querySelector('input:not([readonly])');
            const correct = !input || input.value === ${JSON.stringify(step.expected)};
            await fetch('/fixture-submit', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ field: ${JSON.stringify(step.field)}, correct }) });
            if (correct && ${JSON.stringify(step.next ?? null)}) location.href = ${JSON.stringify(step.next ?? null)};
          };</script></body></html>` });
      });
      const page = await context.newPage();
      page.on("pageerror", error => pageErrors.push(error.name));
      const initial = scenario.negative === "email" ? "/verify-email" :
        ["mismatch", "missing", "ambiguous"].includes(scenario.negative) ? "/log-in/password" : "/log-in";
      await page.goto((scenario.negative === "foreign" ? "https://untrusted.invalid" : origin) + prefix + initial,
        { waitUntil: "networkidle" });
      const result = await automateLogin(page, credential, { locale: scenario.locale, callback, now: () => 59_000, timeoutMs: 15_000 });
      const completedFlow = !scenario.negative || scenario.negative === "state";
      assert.equal(result.status, !scenario.negative ? "callback_received" :
        scenario.negative === "mismatch" ? "account_mismatch" : "verification_required");
      assert.deepEqual(actions, completedFlow ? ["email", "password", "code", "consent"] : []);
      assert.equal(callbacks, completedFlow ? 1 : 0);
      assert.deepEqual(unexpected, []);
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(routeFailures, []);
      console.log("OFFLINE_REAL_BROWSER", engine, scenario.name, result.status, "submissions", actions.length, "PASS");
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
}
console.log("OFFLINE_REAL_BROWSER_COMPLETE", engine, cases.length, "no_real_credentials=true no_live_oauth=true browser_closed=true");
