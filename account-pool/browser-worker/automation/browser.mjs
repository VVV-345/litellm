// 沿用 PR #4 的 Firefox 代理安全选项，在服务端临时上下文中运行受限登录。
import { execFile, execFileSync } from "node:child_process";
import { writeFileSync, rmSync } from "node:fs";
import { promisify } from "node:util";
import { setTimeout } from "node:timers/promises";
import { firefox } from "playwright-core";
import { automateLogin, parseCredential, verifyResumableAccount } from "./login.mjs";
import { validateAuthorization } from "./core.mjs";
import { BrowserControl, createControlServer } from "./control.mjs";

const exec = promisify(execFile);
const readyFile = "/tmp/oauth-browser-ready";
const automatic = process.env.OAUTH_AUTOMATION === "true";
const deadline = Date.parse(process.env.OAUTH_BROWSER_EXPIRES_AT ?? "") || Date.now() + 600_000;
const input = async (enabled) => {
  await exec("x11vnc", ["-display", ":99", "-sync", "-R", enabled ? "noviewonly" : "viewonly"], {
    timeout: 5000, env: { DISPLAY: ":99", HOME: "/tmp", PATH: process.env.PATH },
  });
};

async function main() {
  if (process.env.CHROME_PROXY !== "http://egress-relay:8080") throw new Error("proxy");
  const authorization = process.env.OAUTH_AUTHORIZATION_URL;
  const trusted = automatic ? validateAuthorization(authorization) : null;
  const options = JSON.parse(execFileSync("python", ["/app/camoufox_profile.py"], { encoding: "utf8", maxBuffer: 2_000_000 }));
  const browser = await firefox.launch(options);
  const context = await browser.newContext();
  const page = await context.newPage();
  let controller = null;
  let server = null;
  const stop = new AbortController();
  process.once("SIGTERM", () => stop.abort());
  process.once("SIGINT", () => stop.abort());
  try {
    if (automatic) await input(false);
    await page.goto(authorization, { waitUntil: "domcontentloaded", timeout: 30_000 });
    if (automatic) {
      if (new URL(page.url()).origin === "https://auth.openai.com") {
        process.stderr.write(`OAuth email form readiness: labelled=${await page.getByLabel("Email address", { exact: true }).count()}, typed=${await page.locator('input[type="email"]').count()}\n`);
      }
      const callback = new URL(trusted.callback);
      callback.searchParams.set("state", trusted.url.searchParams.get("state"));
      server = createControlServer({
        token: process.env.OAUTH_AUTOMATION_TOKEN,
        createController: async (text) => {
          const credential = parseCredential(text);
          const submitted = new Set();
          controller = new BrowserControl({
            login: async (signal) => {
              const result = await automateLogin(page, credential, { signal, callback: callback.href, submitted });
              if (result.status === "verification_required") {
                const route = (path) => ["/log-in", "/log-in/password", "/oauth/authorize", "/authorize", "/sign-in-with-chatgpt/codex/consent"].includes(path) ? path : path.startsWith("/mfa-challenge/") ? "authenticator" : "other";
                const state = await page.evaluate(() => ({ path: location.pathname, inputs: document.querySelectorAll("input").length }));
                process.stderr.write(`OAuth form state: ${JSON.stringify({ route: route(new URL(page.url()).pathname), documentRoute: route(state.path), inputs: state.inputs, labelled: await page.getByLabel("Email address", { exact: true }).count(), typed: await page.locator('input[type="email"]').count(), text: await page.locator('input[type="text"]').count(), frames: page.frames().length })}\n`);
              }
              return result;
            },
            verifyAccount: () => verifyResumableAccount(page, credential),
            enableInput: input, close: () => context.close(),
            totalDeadline: deadline, humanMs: Number(process.env.OAUTH_HUMAN_TIMEOUT_SECONDS ?? "300") * 1000,
          });
          return controller;
        },
      });
      await new Promise((resolve) => server.listen(8094, "0.0.0.0", resolve));
    }
    writeFileSync(readyFile, "ready", { mode: 0o600 });
    while (!stop.signal.aborted && Date.now() < deadline && browser.isConnected()) {
      if (controller) await controller.command("status");
      await setTimeout(500, undefined, { signal: stop.signal }).catch(() => {});
    }
  } finally {
    rmSync(readyFile, { force: true });
    if (automatic) await input(false).catch(() => {});
    if (server) await new Promise((resolve) => server.close(resolve));
    if (controller) await controller.command("cancel").catch(() => {});
    await browser.close();
  }
}

main().catch(() => {
  process.stderr.write("OAuth browser driver failed; inspect the isolated worker health and pinned image\n");
  process.exitCode = 1;
});
