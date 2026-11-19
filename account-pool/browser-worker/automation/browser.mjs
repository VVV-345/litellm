// 沿用 PR #4 的 Firefox 代理安全选项，在服务端临时上下文中运行受限登录。
import { execFile, execFileSync } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";
import { promisify } from "node:util";
import { setTimeout } from "node:timers/promises";
import { firefox } from "playwright-core";
import { parseCredential } from "./login.mjs";
import { loadLoginScript, observeLoginPage, toPageDiagnostic } from "./login_script.mjs";
import { openAuthorizationPage, runStartupStage, validateAuthorization } from "./core.mjs";
import { BrowserControl, createControlServer } from "./control.mjs";
import { rebindEmail, createRebindingControl } from "./rebinding.mjs";

const exec = promisify(execFile);
const readyFile = "/tmp/oauth-browser-ready";
const automatic = process.env.OAUTH_AUTOMATION === "true";
const binding = automatic && process.env.OAUTH_BROWSER_PURPOSE === "email_rebind";
const deadline = Date.parse(process.env.OAUTH_BROWSER_EXPIRES_AT ?? "") || Date.now() + 600_000;
const input = async (enabled) => {
  await exec("x11vnc", ["-display", ":99", "-sync", "-R", enabled ? "noviewonly" : "viewonly"], {
    timeout: 5000, env: { DISPLAY: ":99", HOME: "/tmp", PATH: process.env.PATH },
  });
};

async function main() {
  let browser = null;
  let controller = null;
  let server = null;
  let loginScript = null;
  const stop = new AbortController();
  process.once("SIGTERM", () => stop.abort());
  process.once("SIGINT", () => stop.abort());
  try {
    await runStartupStage("driver", async () => {
      if (process.env.CHROME_PROXY !== "http://egress-relay:8080") throw new Error("proxy");
      const authorization = process.env.OAUTH_AUTHORIZATION_URL;
      if (binding && authorization !== "https://chatgpt.com/auth/login") throw new Error("binding_origin");
      const trusted = automatic && !binding ? validateAuthorization(authorization) : null;
      if (automatic) {
        loginScript = await runStartupStage("login_script", () => loadLoginScript(process.env.OAUTH_LOGIN_SCRIPT_PACKAGE, binding ? { rebindEmail } : null));
      }
      const options = await runStartupStage("profile", async () => JSON.parse(execFileSync(
        "python", ["/app/camoufox_profile.py"], { encoding: "utf8", maxBuffer: 2_000_000 },
      )));
      const locale = automatic ? JSON.parse(process.env.OAUTH_FINGERPRINT_PROFILE).locale : "en-US";
      browser = await runStartupStage("browser_launch", () => firefox.launch(options));
      const context = await browser.newContext({ viewport: null });
      const page = await context.newPage();
      const loginDiagnostics = automatic ? observeLoginPage(page, locale) : null;
      if (automatic) await input(false);
      await openAuthorizationPage(page, authorization, process.env.OAUTH_BROWSER_NAVIGATION_TIMEOUT_SECONDS);
      if (loginDiagnostics) {
        if (new URL(page.url()).origin === "https://auth.openai.com") {
          process.stderr.write(`OAuth email form readiness: labelled=${await page.getByLabel("Email address", { exact: true }).count()}, typed=${await page.locator('input[type="email"]').count()}\n`);
        }
        const callback = binding ? null : new URL(trusted.callback);
        if (callback) callback.searchParams.set("state", trusted.url.searchParams.get("state"));
        server = createControlServer({
          token: process.env.OAUTH_AUTOMATION_TOKEN,
          loginScriptDigest: loginScript.digest, purpose: binding ? "email_rebind" : "oauth",
          createController: async (text, target) => {
            const credential = parseCredential(text);
            if (binding) {
              controller = createRebindingControl({ page, account: credential, input, close: () => context.close(),
                script: loginScript, locale, deadline, humanMs: Number(process.env.OAUTH_HUMAN_TIMEOUT_SECONDS ?? "300") * 1000, target });
              return controller;
            }
            const submitted = new Set();
            controller = new BrowserControl({
              login: async (signal) => {
                const result = await loginScript.automateLogin(page, credential, { signal, callback: callback.href, locale, submitted });
                if (result.status === "verification_required") {
                  const status = await loginDiagnostics.inspect().catch(() => ({
                    step: "login", reason: "page_observation_failed", httpStatus: null,
                  }));
                  process.stderr.write(`OAuth form state: ${JSON.stringify(status)}\n`);
                  return { status: result.status, diagnostic: toPageDiagnostic(status) };
                }
                return { status: result.status };
              },
              observe: loginDiagnostics.current,
              verifyAccount: () => loginScript.verifyResumableAccount(page, credential, { locale }),
              enableInput: input, close: () => context.close(),
              totalDeadline: deadline, humanMs: Number(process.env.OAUTH_HUMAN_TIMEOUT_SECONDS ?? "300") * 1000,
            });
            return controller;
          },
        });
        await runStartupStage("control_listener", () => new Promise((resolve, reject) => {
          server.once("error", reject);
          server.listen(8094, "0.0.0.0", () => {
            server.off("error", reject);
            resolve();
          });
        }));
      }
      writeFileSync(readyFile, "ready", { mode: 0o600 });
    });
    while (!stop.signal.aborted && Date.now() < deadline && browser.isConnected()) {
      if (controller) await controller.command("status");
      await setTimeout(500, undefined, { signal: stop.signal }).catch(() => {});
    }
  } finally {
    rmSync(readyFile, { force: true });
    if (automatic) await input(false).catch(() => {});
    if (server) await new Promise((resolve) => server.close(resolve));
    if (controller) await controller.command("cancel").catch(() => {});
    if (browser) await browser.close();
    await loginScript?.cleanup();
  }
}

main().catch(() => {
  process.stderr.write("OAuth browser driver failed; inspect the isolated worker health and pinned image\n");
  process.exitCode = 1;
});
