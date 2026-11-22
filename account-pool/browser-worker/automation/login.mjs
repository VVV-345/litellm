// 复用 PR #4 的受限 Codex 登录与 TOTP；未知验证交给人工，不保存 Cookie。
import { createHmac } from "node:crypto";
import { setTimeout } from "node:timers/promises";

export const AUTOMATION_LOGIN_TIMEOUT_MS = 600_000;

export function authPathname(pathname) {
  return pathname.replace(/^\/[a-z]{2}(?:-[a-z]{2,4}){0,2}\//i, "/");
}

function decodeSecret(value) {
  const text = value.toUpperCase().replace(/=+$/, "");
  if (!/^[A-Z2-7]{16,128}$/.test(text)) throw new Error("credential");
  const bits = [...text].map((char) => "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".indexOf(char).toString(2).padStart(5, "0")).join("");
  return Buffer.from(bits.match(/.{8}/g).map((byte) => parseInt(byte, 2)));
}

export function parseCredential(text) {
  if (typeof text !== "string" || Buffer.byteLength(text) > 8192) throw new Error("credential");
  const records = text.split(/\r?\n/).filter((line) => line.includes("----"));
  if (records.length !== 1) throw new Error("credential");
  const fields = records[0].trim().split("----");
  if (fields.length !== 3) throw new Error("credential");
  const [email, password, totpSecret] = fields;
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !password || password.length > 4096) throw new Error("credential");
  decodeSecret(totpSecret);
  return { email, password, totpSecret };
}

export function totp(secret, now = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now / 30_000)));
  const digest = createHmac("sha1", decodeSecret(secret)).update(counter).digest();
  const value = digest.readUInt32BE(digest[19] & 15) & 0x7fffffff;
  return String(value % 1_000_000).padStart(6, "0");
}

const loginLabels = {
  en: ["Email address", "One-time code", /authenticator app/iu, "Continue"],
  zh: ["电子邮件地址", "一次性代码", /身份验证器/iu, "继续"],
  "zh-TW": ["電子郵件地址", "一次性驗證碼", /驗證器/iu, "繼續"],
  ja: ["メールアドレス", "ワンタイムコード", /認証アプリ/iu, "続行"],
  ko: ["이메일 주소", "일회용 코드", /인증 앱/iu, "계속"],
  fr: ["Adresse e-mail", "Code à usage unique", /application d[’']authentification/iu, "Continuer"],
  de: ["E-Mail-Adresse", "Einmalcode", /Authentifizierungs-App/iu, "Weiter"],
  es: ["Correo electrónico", "Código de un solo uso", /aplicación de autenticación/iu, "Continuar"],
  pt: ["Endereço de e-mail", "Código de uso único", /aplicativo autenticador/iu, "Continuar"],
  ru: ["Адрес электронной почты", "Одноразовый код", /приложени[ея][ -]аутентификатор/iu, "Продолжить"],
  tr: ["E-posta adresi", "Tek kullanımlık kod", /kimlik doğrulama uygulama/iu, "Devam et"],
  uk: ["Електронна адреса", "Одноразовий код", /(?:застосунок|додаток)[ -]автентифікатор/iu, "Продовжити"],
};

function labelsFor(locale) {
  const key = /^zh-(?:TW|HK|Hant)(?:-|$)/i.test(locale) ? "zh-TW" : locale.split("-")[0];
  return loginLabels[key] ?? loginLabels.en;
}

async function loginInput(page, field, labels) {
  const english = page.getByLabel(field, { exact: true });
  if (await english.isVisible()) return english;
  const translated = field === "Email address" ? labels[0] : field === "One-time code" ? labels[1] : field;
  const labelled = page.getByLabel(translated, { exact: true });
  if (await labelled.isVisible()) return labelled;
  const selector = field === "Email address" ? 'input[type="email"]'
    : field === "Password" ? 'input[type="password"]' : 'input[autocomplete="one-time-code"]';
  return page.locator(selector);
}

async function passwordAccountMatches(page, credential, labels) {
  try {
    const input = await loginInput(page, "Email address", labels);
    const count = await input.count();
    if (count > 1) return null;
    if (count === 1) {
      const email = await input.inputValue({ timeout: 3000 });
      return email ? email.toLowerCase() === credential.email.toLowerCase() : null;
    }
    // 不存在邮箱输入框时，只接受官网上唯一、可见的完整账号；错误的已有字段不能被此分支覆盖。
    await page.getByText(credential.email, { exact: true }).waitFor({ state: "visible", timeout: 3000 });
    return true;
  } catch {
    return null;
  }
}

export async function automateLogin(page, credential, {
  signal, allowConsent = true, callback, locale = "en-US", now = Date.now, timeoutMs = AUTOMATION_LOGIN_TIMEOUT_MS,
  challengeMs = 8000, submitted = new Set(),
} = {}) {
  const deadline = Date.now() + timeoutMs;
  const labels = labelsFor(locale);
  const stopped = () => signal?.aborted || page.isClosed();
  const visible = async (locator) => locator.isVisible();
  // OpenAI 登录页由 Cloudflare Turnstile 拦截，自动挑战不放行时必须交回人工接管。
  const challengePending = async () => {
    try {
      return await page.evaluate(() => {
        const frame = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
        const text = document.body ? document.body.innerText : "";
        return Boolean(frame) || /安全验证|Just a moment|请稍候|Verify you are human/i.test(text);
      });
    } catch {
      return false;
    }
  };
  let challengeAt = 0;
  const heading = (name) => page.getByRole("heading", { name, exact: true });
  const accountMatches = async () => {
    try {
      await page.getByText(credential.email, { exact: true }).waitFor({ state: "visible", timeout: 3000 });
      return true;
    } catch {
      return false;
    }
  };
  const result = (status) => ({ status });
  const formAction = async (step, action, run) => {
    try {
      return await run() ?? null;
    } catch (error) {
      return stopped() ? result("stopped") : { status: "verification_required", diagnostic: {
        step, reason: `form_${action}_${error?.name === "TimeoutError" ? "timeout" : "failed"}`, http_status: null,
      } };
    }
  };
  try {
    while (Date.now() < deadline) {
      if (stopped()) return result("stopped");
      const current = new URL(page.url());
      if (callback) {
        const expected = new URL(callback);
        if (current.origin === expected.origin && current.pathname === expected.pathname) {
          return result(current.searchParams.get("state") === expected.searchParams.get("state") &&
            !!current.searchParams.get("code") && !current.searchParams.has("error") ? "callback_received" : "verification_required");
        }
      }
      if (current.origin !== "https://auth.openai.com") return result("verification_required");
      if (await challengePending()) {
        challengeAt ||= Date.now();
        if (Date.now() - challengeAt > challengeMs) return result("verification_required");
        await setTimeout(250, undefined, { signal });
        continue;
      }
      challengeAt = 0;
      const path = authPathname(current.pathname);
      if (await hasInlineAuthError(page)) {
        return { status: "verification_required" };
      }
      const unchanged = () => !stopped() && page.url() === current.href;
      if (path === "/sign-in-with-chatgpt/codex/consent" && !submitted.has(path)) {
        if (!await accountMatches()) return result("account_mismatch");
        if (!allowConsent) return result("confirmation_required");
        // 标题会随语言变化；仍要求可信路径、同一账号、应用品牌和绑定的一次性回调。
        const title = await page.title();
        if (!callback || !/^(?=.*\bCodex\b)(?=.*\bChatGPT\b).* - OpenAI$/.test(title)) return result("verification_required");
        if (!unchanged()) return result("stopped");
        const failure = await formAction("consent", "submit", async () => {
          const english = page.getByRole("button", { name: "Continue", exact: true });
          const button = await visible(english) ? english : page.getByRole("button", { name: labels[3], exact: true });
          if (!unchanged()) return result("stopped");
          submitted.add(path);
          await button.click({ timeout: 3000, noWaitAfter: true });
        });
        if (failure) return failure;
        continue;
      }
      const field = path === "/log-in" ? "Email address" : path === "/log-in/password" ? "Password" : path.startsWith("/mfa-challenge/") ? "One-time code" : null;
      if (!field && !submitted.has(path) && !["/oauth/authorize", "/authorize"].includes(path)) return result("verification_required");
      const step = field === "Email address" ? "email" : field === "Password" ? "password" : "authenticator";
      const form = field ? await formAction(step, "fill", async () => {
        const input = await loginInput(page, field, labels);
        return { input, visible: await visible(input) };
      }) : null;
      if (form?.status) return form;
      if (field && form?.visible && !submitted.has(path)) {
        const input = form.input;
        if (field === "Password") {
          const matches = await passwordAccountMatches(page, credential, labels);
          if (matches !== true) return result(matches === false ? "account_mismatch" : "verification_required");
        }
        if (field === "One-time code" && ((!submitted.has("/log-in/password") && !await accountMatches()) ||
            (!await visible(heading(loginLabels.en[2])) && !await visible(heading(labels[2]))))) return result("verification_required");
        if (!unchanged()) return result("stopped");
        if (now === Date.now && field !== "Email address" && field !== "Password") {
          const stopSignal = typeof signal !== "undefined" ? signal : undefined;
          const remainingMs = 30_000 - (now() % 30_000);
          if (remainingMs < 3_000) {
            await sleepWithSignal(remainingMs + 250, stopSignal);
            if (stopSignal?.aborted) return { status: "stopped" };
          }
        }
        const value = field === "Email address" ? credential.email : field === "Password" ? credential.password : totp(credential.totpSecret, now());
        const fillFailure = await formAction(step, "fill", async () => { await input.fill(value, { timeout: 3000 }); });
        if (fillFailure) return fillFailure;
        const submitFailure = await formAction(step, "submit", async () => {
          const english = page.getByRole("button", { name: "Continue", exact: true });
          const button = await visible(english) ? english : page.getByRole("button", { name: labels[3], exact: true });
          const canClick = await visible(button);
          if (!unchanged()) return result("stopped");
          // 有些登录页只响应按钮点击；一旦尝试提交，不再用另一种方式重发。
          submitted.add(path);
          if (canClick) await button.click({ timeout: 3000, noWaitAfter: true });
          else await input.press("Enter", { timeout: 3000, noWaitAfter: true });
        });
        if (submitFailure) return submitFailure;
        continue;
      }
      await setTimeout(250, undefined, { signal });
    }
    return result("verification_required");
  } catch {
    return result(stopped() ? "stopped" : "verification_required");
  }
}

export async function verifyResumableAccount(page, credential, { locale = "en-US" } = {}) {
  const labels = labelsFor(locale);
  try {
    const current = new URL(page.url());
    if (current.origin !== "https://auth.openai.com") return false;
    const path = authPathname(current.pathname);
    if (path === "/log-in") {
      if (await hasSecurityChallenge(page)) return false;
      const emailValue = await readLoginEmailInput(page);
      return emailValue === "";
    }
    if (path === "/log-in/password") return await passwordAccountMatches(page, credential, labels) === true;
    if (path !== "/sign-in-with-chatgpt/codex/consent" && !path.startsWith("/mfa-challenge/")) return false;
    await page.getByText(credential.email, { exact: true }).waitFor({ state: "visible", timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}

const INLINE_AUTH_ERROR_PATTERN = /incorrect|wrong|invalid|too many|rate[- ]?limit|try again|suspended|blocked|locked|disabled|something went wrong|错误|失败|频繁|停用|限制/iu;

async function hasInlineAuthError(page) {
  if (typeof page?.locator !== "function") return false;
  try {
    const alert = page.locator('[role="alert"]').first();
    if (typeof alert?.isVisible !== "function" || !(await alert.isVisible().catch(() => false))) return false;
    if (typeof alert?.innerText !== "function") return false;
    const text = (await alert.innerText().catch(() => ""))?.trim() ?? "";
    return text.length > 0 && INLINE_AUTH_ERROR_PATTERN.test(text);
  } catch {
    return false;
  }
}

async function hasSecurityChallenge(page) {
  if (typeof page?.evaluate !== "function") return false;
  try {
    return Boolean(await page.evaluate(() => Boolean(
      document.querySelector('iframe[src*="challenges.cloudflare.com"], #challenge-running')
    )).catch(() => false));
  } catch {
    return false;
  }
}

async function readLoginEmailInput(page) {
  const candidates = [];
  if (typeof page?.locator === "function") {
    candidates.push(page.locator('input[type="email"], input[name="email"]')?.first?.());
  }
  if (typeof page?.getByLabel === "function") {
    candidates.push(page.getByLabel("Email address")?.first?.());
  }
  for (const field of candidates) {
    if (!field || typeof field.inputValue !== "function") continue;
    try {
      const visible = typeof field.isVisible === "function" ? await field.isVisible().catch(() => false) : true;
      if (!visible) continue;
      const value = await field.inputValue();
      if (typeof value === "string") return value.trim().toLowerCase();
    } catch {
      continue;
    }
  }
  return null;
}

function sleepWithSignal(ms, signal) {
  if (ms <= 0 || signal?.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = globalThis.setTimeout(() => {
      signal?.removeEventListener?.("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      globalThis.clearTimeout(timer);
      resolve();
    }
    signal?.addEventListener?.("abort", onAbort, { once: true });
  });
}
