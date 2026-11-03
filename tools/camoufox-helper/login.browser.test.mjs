import test from "node:test";
import assert from "node:assert/strict";
import { setTimeout } from "node:timers/promises";
import { firefox } from "playwright-core";
import { automateLogin } from "./login.mjs";

test("waits for slow navigation and consent rendering without resubmitting login", {
  skip: !process.env.CAMOUFOX_TEST_EXECUTABLE,
}, async () => {
  const browser = await firefox.launch({ executablePath: process.env.CAMOUFOX_TEST_EXECUTABLE, headless: true });
  try {
    const page = await browser.newPage();
    const submissions = [];
    await page.route("**/*", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/log-in") {
        await route.fulfill({ contentType: "text/html", body: '<form method="post" action="/sign-in-with-chatgpt/codex/consent"><label>Email address<input name="email"></label><button>Continue</button></form>' });
      } else if (path === "/sign-in-with-chatgpt/codex/consent") {
        submissions.push(route.request().postData());
        await setTimeout(4000);
        await route.fulfill({ contentType: "text/html", body: '<title>Sign in to Codex with ChatGPT - OpenAI</title><script>setTimeout(() => { document.body.innerHTML = "<p>account@example.test</p><button>Continue</button>"; }, 500)</script>' });
      } else await route.abort();
    });
    await page.goto("https://auth.openai.com/log-in");
    const result = await automateLogin(page, {
      email: "account@example.test", password: "test-password", totpSecret: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    });
    assert.deepEqual(result, { status: "confirmation_required" });
    assert.deepEqual(submissions, ["email=account%40example.test"]);
  } finally {
    await browser.close();
  }
});
