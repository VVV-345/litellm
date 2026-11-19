// 只在已核对账号的官网执行换邮箱；未知页面交人工，已提交的步骤不重复执行。
import { setTimeout } from "node:timers/promises";
import { automateLogin } from "./login.mjs";
import { BrowserControl } from "./control.mjs";

export async function webIdentity(page) {
  if (new URL(page.url()).origin !== "https://chatgpt.com") return null;
  try {
    const result = await page.evaluate(async () => {
      const response = await fetch("/api/auth/session", { credentials: "same-origin", redirect: "error" });
      if (!response.ok) return null;
      const session = await response.json();
      return { subject: session?.user?.id, email: session?.user?.email };
    });
    return typeof result?.subject === "string" && result.subject.length <= 256 &&
      typeof result.email === "string" && result.email.length <= 320 ? result : null;
  } catch { return null; }
}

const text = {
  en: { login: "Log in", password: "Use password", send: "Send verification email", verify: "Verify" },
  ja: { login: "ログイン", password: "パスワードを使用", send: "確認メールを送信", verify: "確認" },
  zh: { login: "登录", password: "使用密码", send: "发送验证邮件", verify: "验证" },
};
const labels = locale => text[locale.split("-")[0]] ?? text.en;

async function button(page, values) {
  for (const name of values) {
    const locator = page.getByRole("button", { name, exact: true });
    if (await locator.count() === 1 && await locator.isVisible()) return locator;
  }
  return null;
}

export async function loginWeb(page, account, { signal, locale, submitted }) {
  const initial = await webIdentity(page);
  if (initial) return initial.email.toLowerCase() === account.email.toLowerCase() ? initial : null;
  if (new URL(page.url()).origin === "https://chatgpt.com") {
    await page.getByRole("button", { name: labels(locale).login, exact: true })
      .or(page.getByRole("button", { name: text.en.login, exact: true })).first()
      .waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
    if (signal.aborted) return null;
    const login = await button(page, [labels(locale).login, text.en.login]);
    if (!login) return null;
    await login.click({ timeout: 3000, noWaitAfter: true });
    await page.waitForURL(url => url.origin === "https://auth.openai.com", { timeout: 10000 }).catch(() => {});
  }
  if (new URL(page.url()).origin === "https://auth.openai.com") {
    await automateLogin(page, account, { signal, locale, submitted, allowConsent: false, timeoutMs: 30000 });
  }
  const result = await webIdentity(page);
  return result?.email.toLowerCase() === account.email.toLowerCase() ? result : null;
}

export async function rebindEmail(page, account, options) {
  const { signal, locale, targetEmail, state, checkpoint, receiveCode, submitted } = options;
  if (state.submitted) return { status: "verification_required" };
  const identity = await loginWeb(page, account, { signal, locale, submitted });
  if (!identity) return { status: "verification_required" };
  if (!await checkpoint(identity)) return { status: "stopped" };
  if (signal.aborted) return { status: "stopped" };
  await page.goto("https://chatgpt.com/#settings/Account", { waitUntil: "commit", timeout: 30000 });
  const visibleEmail = page.getByText(account.email, { exact: true });
  await visibleEmail.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  if (signal.aborted) return { status: "stopped" };
  if (await visibleEmail.count() !== 1 || !await visibleEmail.isVisible()) return { status: "verification_required" };
  await visibleEmail.click({ timeout: 3000 });
  const usePassword = await button(page, [labels(locale).password, text.en.password]);
  if (usePassword) {
    await usePassword.click({ timeout: 3000, noWaitAfter: true });
    const password = page.locator('input[type="password"]');
    await password.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
    if (signal.aborted) return { status: "stopped" };
    if (await password.count() !== 1 || !await password.isVisible()) return { status: "verification_required" };
    await password.fill(account.password, { timeout: 3000 });
    await password.press("Enter", { timeout: 3000, noWaitAfter: true });
  }
  const address = page.locator('input[type="email"]');
  await address.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  if (signal.aborted) return { status: "stopped" };
  if (await address.count() !== 1 || !await address.isVisible()) return { status: "verification_required" };
  await address.fill(targetEmail, { timeout: 3000 });
  const send = await button(page, [labels(locale).send, text.en.send]);
  if (!send || signal.aborted) return { status: "verification_required" };
  state.submitted = true;
  state.step = "changing";
  await send.click({ timeout: 3000, noWaitAfter: true });
  const codeInput = page.locator('input[autocomplete="one-time-code"]');
  await codeInput.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
  if (await codeInput.count() !== 1 || !await codeInput.isVisible() ||
      !await page.getByText(targetEmail, { exact: false }).isVisible()) return { status: "verification_required" };
  const code = await receiveCode();
  if (!code || signal.aborted) return { status: "stopped" };
  await codeInput.fill(code, { timeout: 3000 });
  const verify = await button(page, [labels(locale).verify, text.en.verify]);
  if (!verify || signal.aborted) return { status: "verification_required" };
  state.step = "verifying";
  await verify.click({ timeout: 3000, noWaitAfter: true });
  for (let attempt = 0; attempt < 10 && !signal.aborted; attempt += 1) {
    const after = await webIdentity(page);
    if (after?.email.toLowerCase() === targetEmail.toLowerCase()) return { status: "done" };
    await setTimeout(500, undefined, { signal }).catch(() => {});
  }
  await page.goto("https://chatgpt.com/auth/login", { waitUntil: "commit", timeout: 30000 });
  await loginWeb(page, { ...account, email: targetEmail }, { signal, locale, submitted: new Set() });
  return { status: "done" };
}

export function createRebindingControl({ page, account, input, close, script, locale, deadline, humanMs, target }) {
  const state = { step: "login", submitted: Boolean(target.verify_only), before: null };
  const submitted = new Set();
  let releaseCommit = null;
  let releaseCode = null;
  const wait = (signal, setResolver) => new Promise(resolve => {
    if (signal.aborted) { resolve(null); return; }
    const done = value => { signal.removeEventListener("abort", abort); setResolver(null); resolve(value); };
    const abort = () => done(null);
    setResolver(done);
    signal.addEventListener("abort", abort, { once: true });
  });
  const validAfter = async () => {
    const identity = await webIdentity(page);
    return identity?.email.toLowerCase() === target.email.toLowerCase() &&
      identity.subject === state.before?.subject;
  };
  const control = new BrowserControl({
    enableInput: input, close, totalDeadline: deadline, humanMs,
    verifyAccount: async () => {
      const identity = await webIdentity(page);
      return identity !== null && (!state.before || identity.subject === state.before.subject) &&
        [account.email.toLowerCase(), target.email.toLowerCase()].includes(identity.email.toLowerCase());
    },
    login: async signal => {
      if (target.verify_only) {
        state.before = { subject: target.subject, email: account.email };
        await loginWeb(page, { ...account, email: target.email }, { signal, locale, submitted });
      } else if (!state.submitted && !await validAfter()) {
        await script.rebindEmail(page, account, {
          signal, locale, targetEmail: target.email, state, submitted,
          checkpoint: async identity => {
            const observed = await webIdentity(page);
            if (!observed || observed.email.toLowerCase() !== account.email.toLowerCase() ||
                observed.subject !== identity.subject || identity.email.toLowerCase() !== account.email.toLowerCase()) return false;
            if (state.before && state.before.subject !== identity.subject) return false;
            state.before = identity;
            state.step = "awaiting_commit";
            const allowed = Boolean(await wait(signal, value => { releaseCommit = value; }));
            if (allowed) state.submitted = true;
            return allowed;
          },
          receiveCode: async () => {
            state.step = "waiting_code";
            return wait(signal, value => { releaseCode = value; });
          },
        });
      }
      if (await validAfter()) { state.step = "changed"; return { status: "callback_received" }; }
      return { status: "verification_required" };
    },
  });
  return {
    async command(action, payload = {}) {
      if (action === "commit") {
        if (state.step !== "awaiting_commit" || !releaseCommit) throw new Error("unexpected_commit");
        state.step = "changing";
        releaseCommit(true);
      } else if (action === "code") {
        if (state.step !== "waiting_code" || !releaseCode || !/^[0-9]{6}$/.test(payload.code)) throw new Error("unexpected_code");
        state.step = "verifying";
        releaseCode(payload.code);
      }
      const view = await control.command(["commit", "code"].includes(action) ? "status" : action);
      return { ...view, step: state.step, subject: state.before?.subject ?? null,
               source_email: state.before?.email ?? null, target_email: target.email };
    },
    diagnosticView: () => null,
  };
}
