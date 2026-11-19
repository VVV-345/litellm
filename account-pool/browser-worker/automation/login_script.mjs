// 只在隔离 Worker 内展开受信发布包，固定导出契约并保留相对依赖。
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { automateLogin, verifyResumableAccount } from "./login.mjs";

const safePath = (value) => typeof value === "string" && value.length <= 160 &&
  !/[\\:\p{Cc}\p{Cs}]/u.test(value) && value.split("/").every(part => part && part !== "." && part !== "..");

export async function loadLoginScript(payload, bindingDefault = null) {
  if (payload === undefined) return { ...(bindingDefault ?? { automateLogin, verifyResumableAccount }), digest: null, cleanup: async () => {} };
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
    if (bindingDefault ? typeof loaded.rebindEmail !== "function" :
        typeof loaded.automateLogin !== "function" || typeof loaded.verifyResumableAccount !== "function") {
      throw new Error("login_script_exports");
    }
    const digest = createHash("sha256").update(payload).digest("hex");
    return bindingDefault ? { rebindEmail: loaded.rebindEmail, digest, cleanup }
      : { automateLogin: loaded.automateLogin, verifyResumableAccount: loaded.verifyResumableAccount, digest, cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}

const trustedAuth = "https://auth.openai.com";
const routes = new Map([
  ["/log-in", "email"], ["/log-in/password", "password"],
  ["/oauth/authorize", "authorize"], ["/authorize", "authorize"],
  ["/sign-in-with-chatgpt/codex/consent", "consent"],
]);
export function observeLoginPage(page, profileLocale = "en-US") {
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
  const location = () => {
    const url = new URL(page.url());
    const origin = url.origin === trustedAuth ? "openai_auth" : "other";
    const path = url.pathname.replace(/^\/[a-z]{2}(?:-[a-z]{2,4}){0,2}\//i, "/");
    const route = origin !== "openai_auth" ? "other" : routes.has(path) ? path
      : path.startsWith("/mfa-challenge/") ? "authenticator"
      : /(?:^|\/)(?:email-verification|verify-email)(?:\/|$)/.test(path) ? "email_verification" : "other";
    const httpStatus = lastNavigation?.url === url.href ? lastNavigation.status : null;
    const step = routes.get(route) ?? (route === "other" ? "login" : route);
    const reason = origin !== "openai_auth" ? "unexpected_origin"
      : httpStatus >= 400 ? "navigation_http_error"
      : route === "email_verification" ? "email_verification"
      : route === "other" ? "unrecognized_route" : null;
    return { origin, route, step, httpStatus, reason };
  };
  const inspect = async () => {
    const url = page.url();
    const current = location();
    const empty = { inputs: null, challenge: false };
    if (current.origin !== "openai_auth") return { ...current, ...empty };
    const { inputs, challenge, email } = await page.evaluate(fallback => {
      const lang = (document.documentElement.lang.trim() || fallback).toLowerCase().split("-")[0];
      const heading = document.querySelector('h1, [role="heading"][aria-level="1"]')?.textContent?.trim() ?? "";
      const title = document.title.trim();
      const emailHeading = /^(?:check your (?:email|inbox)|verify your email|请(?:查看|验证)(?:您的|你的)?邮箱)/i.test(heading)
        || (lang === "ja" && /^メール(?:アドレス)?を確認/.test(heading))
        || (lang === "pt" && /^(?:verifique|confira) (?:o )?seu (?:endereço de )?e-?mail\b/iu.test(heading));
      return {
        inputs: document.querySelectorAll("input").length,
        challenge: Boolean(document.querySelector('iframe[src*="challenges.cloudflare.com"], #challenge-running')) ||
          [title, heading].some(text => /^(?:Just a moment|Verify you are human|请稍候)/i.test(text) ||
            (lang === "ja" && /^(?:あなたが)?人間であることを確認/.test(text)) ||
            (lang === "pt" && /^(?:verifi(?:que|car) se|confirme que) você é humano/iu.test(text))),
        email: emailHeading && Boolean(document.querySelector('input[autocomplete="one-time-code"]')),
      };
    }, profileLocale).catch(() => empty);
    if (page.url() !== url) return { ...location(), ...empty };
    if (!challenge && email && current.reason === "unrecognized_route") {
      return { ...current, step: "email_verification", reason: "email_verification", inputs, challenge };
    }
    const reason = challenge ? "browser_security_challenge" : current.reason ?? "form_or_transition_not_confirmed";
    return { ...current, reason, inputs, challenge };
  };
  return { current: () => toPageDiagnostic(location()), inspect };
}

export const toPageDiagnostic = ({ step, reason, httpStatus }) => ({ step, reason, http_status: httpStatus });
