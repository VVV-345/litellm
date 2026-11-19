// 在离线日语页面夹具中用真实浏览器验收换绑选择器、确认闸门和人工验证停止。
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { setTimeout } from "node:timers/promises";
import { rebindEmail, createRebindingControl } from "../automation/rebinding.mjs";

const modulePath = process.argv[2];
const executablePath = process.argv[3];
if (!modulePath || !executablePath) throw new Error("usage: node email_rebinding_browser.mjs PLAYWRIGHT_MODULE BROWSER_EXECUTABLE");
const { chromium } = await import(pathToFileURL(modulePath).href);
const oldEmail = "old@example.test", targetEmail = "new@example.test";
const browser = await chromium.launch({ executablePath, headless: true });
try {
  for (const manual of [false, true]) {
    let identity = { id: "fixture-same-user", email: oldEmail };
    let submissions = 0;
    const context = await browser.newContext({ locale: "ja-JP" });
    try {
      await context.route("**/*", async route => {
        const request = route.request(), url = new URL(request.url());
        assert.equal(url.origin, "https://chatgpt.com", "fixture must not call external services");
        if (url.pathname === "/api/auth/session") {
          await route.fulfill({ json: { user: identity } }); return;
        }
        if (url.pathname === "/fixture/verify") {
          assert.equal(request.postDataJSON().code, "123456");
          assert.equal(request.postDataJSON().email, targetEmail);
          identity = { ...identity, email: targetEmail }; submissions++;
          await route.fulfill({ json: { ok: true } }); return;
        }
        if (["/auth/login", "/"].includes(url.pathname)) {
          const challenge = manual ? '<h1>元のメールアドレスを確認してください</h1><input autocomplete="one-time-code">' :
            '<button id="password">パスワードを使用</button>';
          await route.fulfill({ contentType: "text/html", body: `<!doctype html><html lang="ja"><meta charset="utf-8"><body>
            <button id="account">${oldEmail}</button><main></main><script>
            const main = document.querySelector('main');
            document.querySelector('#account').onclick = () => {
              main.innerHTML = ${JSON.stringify(challenge)};
              const button = document.querySelector('#password');
              if (button) button.onclick = () => {
                main.innerHTML = '<input type="password">';
                document.querySelector('input').onkeydown = event => {
                  if(event.key !== 'Enter') return;
                  if(event.target.value !== 'fixture-site-password') throw Error('wrong original password');
                  main.innerHTML = '<input type="email"><button id="send">確認メールを送信</button>';
                  document.querySelector('#send').onclick = () => {
                    const email = document.querySelector('input').value;
                    main.innerHTML = '<p>'+email+'</p><input autocomplete="one-time-code"><button id="verify">確認</button>';
                    document.querySelector('#verify').onclick = async () => {
                      await fetch('/fixture/verify', { method:'POST', headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({ email, code:document.querySelector('input').value }) });
                    };
                  };
                };
              };
            };
            </script></body></html>` }); return;
        }
        await route.abort();
      });
      const page = await context.newPage();
      await page.goto("https://chatgpt.com/auth/login");
      const control = createRebindingControl({ page, account: { email: oldEmail, password: "fixture-site-password" },
        input: async () => {}, close: async () => {}, script: { rebindEmail }, locale: "ja-JP",
        deadline: Date.now()+60000, humanMs: 10000, target: { email: targetEmail, verify_only: false } });
      const until = async predicate => {
        for (let n=0;n<500;n++) {
          const view = await control.command("status");
          if (predicate(view)) return view;
          if (view.status === "failed") assert.fail(JSON.stringify(view));
          await setTimeout(20);
        }
        assert.fail("browser fixture timed out");
      };
      await control.command("start");
      await until(v => v.step === "awaiting_commit");
      assert.equal(submissions, 0);
      await control.command("commit");
      if (manual) {
        await until(v => v.status === "waiting");
        assert.equal(submissions, 0);
        console.log("JA_BROWSER_ORIGINAL_EMAIL_CHALLENGE stopped_before_change=true no_bypass=true");
      } else {
        await until(v => v.step === "waiting_code");
        await control.command("code", { code: "123456" });
        const done = await until(v => v.status === "callback_received");
        assert.equal(done.subject, identity.id); assert.equal(done.target_email, identity.email);
        assert.equal(submissions, 1);
        await control.command("resume"); assert.equal(submissions, 1);
        console.log("JA_BROWSER_FIXTURE completed=true same_subject=true submissions=1 original_password_preserved=true");
      }
    } finally { await context.close(); }
  }
} finally { await browser.close(); }
console.log("REAL_BROWSER_FIXTURE_ONLY no_real_oauth_or_email_change=true browser_cleaned=true");
