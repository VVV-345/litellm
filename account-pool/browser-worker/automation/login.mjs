// 复用 PR #4 的受限 Codex 登录与 TOTP；未知验证交给人工，不保存 Cookie。
import { createHmac } from "node:crypto";
import { setTimeout } from "node:timers/promises";

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

export async function automateLogin(page, credential, {
  signal, allowConsent = true, callback, locale = "en-US", now = Date.now, timeoutMs = 45_000, submitted = new Set(),
} = {}) {
  const deadline = Date.now() + timeoutMs;
  const labels = labelsFor(locale);
  const stopped = () => signal?.aborted || page.isClosed();
  const visible = async (locator) => locator.isVisible();
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
      const path = current.pathname;
      const unchanged = () => !stopped() && page.url() === current.href;
      if (path === "/sign-in-with-chatgpt/codex/consent" && !submitted.has(path)) {
        if (!await accountMatches()) return result("account_mismatch");
        if (!allowConsent) return result("confirmation_required");
        // 标题会随语言变化；仍要求可信路径、同一账号、应用品牌和绑定的一次性回调。
        const title = await page.title();
        if (!callback || !/^(?=.*\bCodex\b)(?=.*\bChatGPT\b).* - OpenAI$/.test(title)) return result("verification_required");
        if (!unchanged()) return result("stopped");
        submitted.add(path);
        const english = page.getByRole("button", { name: "Continue", exact: true });
        const button = await visible(english) ? english : page.getByRole("button", { name: labels[3], exact: true });
        await button.click({ timeout: 3000, noWaitAfter: true });
        continue;
      }
      const field = path === "/log-in" ? "Email address" : path === "/log-in/password" ? "Password" : path.startsWith("/mfa-challenge/") ? "One-time code" : null;
      if (!field && !submitted.has(path) && !["/oauth/authorize", "/authorize"].includes(path)) return result("verification_required");
      const input = field ? await loginInput(page, field, labels) : null;
      if (field && input && !submitted.has(path) && await visible(input)) {
        if (field === "Password" && (await (await loginInput(page, "Email address", labels)).inputValue({ timeout: 3000 })).toLowerCase() !== credential.email.toLowerCase()) return result("account_mismatch");
        if (field === "One-time code" && ((!submitted.has("/log-in/password") && !await accountMatches()) ||
            (!await visible(heading(loginLabels.en[2])) && !await visible(heading(labels[2]))))) return result("verification_required");
        if (!unchanged()) return result("stopped");
        const value = field === "Email address" ? credential.email : field === "Password" ? credential.password : totp(credential.totpSecret, now());
        await input.fill(value, { timeout: 3000 });
        if (!unchanged()) return result("stopped");
        submitted.add(path);
        await input.press("Enter", { timeout: 3000, noWaitAfter: true });
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
    if (current.pathname === "/log-in/password") {
      const email = await (await loginInput(page, "Email address", labels)).inputValue({ timeout: 3000 });
      return email.toLowerCase() === credential.email.toLowerCase();
    }
    if (current.pathname !== "/sign-in-with-chatgpt/codex/consent" && !current.pathname.startsWith("/mfa-challenge/")) return false;
    await page.getByText(credential.email, { exact: true }).waitFor({ state: "visible", timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}
