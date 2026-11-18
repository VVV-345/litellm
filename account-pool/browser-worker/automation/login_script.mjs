// 只在隔离 Worker 内展开受信发布包，固定导出契约并保留相对依赖。
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { automateLogin, verifyResumableAccount } from "./login.mjs";

const safePath = (value) => typeof value === "string" && value.length <= 160 &&
  !/[\\:\p{Cc}\p{Cs}]/u.test(value) && value.split("/").every(part => part && part !== "." && part !== "..");

export async function loadLoginScript(payload) {
  if (payload === undefined) return { automateLogin, verifyResumableAccount, digest: null, cleanup: async () => {} };
  if (typeof payload !== "string" || Buffer.byteLength(payload) > 120_000) throw new Error("login_script_size");
  const { entrypoint, files } = JSON.parse(payload);
  if (!safePath(entrypoint) || !/\.(?:mjs|js)$/.test(entrypoint) ||
      !Array.isArray(files) || files.length < 1 || files.length > 64) throw new Error("login_script_manifest");
  const names = new Set(files.map(file => file?.path));
  if (names.size !== files.length || !names.has(entrypoint) ||
      !files.every(file => safePath(file?.path) && typeof file.data === "string")) throw new Error("login_script_path");
  const decoded = files.map(({ path, data }) => [path, Buffer.from(data, "base64")]);
  if (decoded.reduce((size, [, bytes]) => size + bytes.length, 0) > 65_536) throw new Error("login_script_size");
  const root = await mkdtemp(join(tmpdir(), "oauth-login-"));
  const cleanup = () => rm(root, { recursive: true, force: true });
  try {
    for (const [path, bytes] of decoded) {
      const dest = join(root, path);
      await mkdir(dirname(dest), { recursive: true });
      await writeFile(dest, bytes, { flag: "wx", mode: 0o600 });
    }
    const loaded = await import(pathToFileURL(join(root, entrypoint)).href);
    if (typeof loaded.automateLogin !== "function" || typeof loaded.verifyResumableAccount !== "function") {
      throw new Error("login_script_exports");
    }
    const digest = createHash("sha256").update(payload).digest("hex");
    return { automateLogin: loaded.automateLogin, verifyResumableAccount: loaded.verifyResumableAccount, digest, cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}

const trustedAuth = "https://auth.openai.com";
const routes = new Set(["/log-in", "/log-in/password", "/oauth/authorize", "/authorize", "/sign-in-with-chatgpt/codex/consent"]);
export function observeLoginPage(page) {
  let lastNavigation = null;
  page.on("response", response => {
    const request = response.request();
    if (!request.isNavigationRequest()) return;
    try {
      if (request.frame() === page.mainFrame()) {
        lastNavigation = { url: response.url(), status: response.status() };
      }
    } catch {
      // Playwright may report the navigation before the frame is attached.
    }
  });
  return async () => {
    const url = new URL(page.url());
    const origin = url.origin === trustedAuth ? "openai_auth" : "other";
    const route = origin !== "openai_auth" ? "other" : routes.has(url.pathname) ? url.pathname
      : url.pathname.startsWith("/mfa-challenge/") ? "authenticator"
      : /(?:^|\/)(?:email-verification|verify-email)(?:\/|$)/.test(url.pathname) ? "email_verification" : "other";
    const httpStatus = lastNavigation?.url === url.href ? lastNavigation.status : null;
    const shape = await page.evaluate(() => ({
      inputs: document.querySelectorAll("input").length,
      challenge: Boolean(document.querySelector('iframe[src*="challenges.cloudflare.com"], #challenge-running')) ||
        /^(?:Just a moment|Verify you are human|请稍候)/i.test(document.title),
    })).catch(() => ({ inputs: null, challenge: false }));
    const reason = shape.challenge ? "browser_security_challenge"
      : route === "email_verification" ? "email_verification"
      : origin !== "openai_auth" ? "unexpected_origin"
      : httpStatus >= 400 ? "navigation_http_error"
      : route === "other" ? "unrecognized_route" : "form_or_transition_not_confirmed";
    return { origin, route, httpStatus, reason, ...shape };
  };
}
