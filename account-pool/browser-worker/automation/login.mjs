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

export async function automateLogin(page, credential, {
  signal, allowConsent = true, callback, now = Date.now, timeoutMs = 45_000, submitted = new Set(),
} = {}) {
  const deadline = Date.now() + timeoutMs;
  const stopped = () => signal?.aborted || page.isClosed();
  const visible = async (locator) => locator.isVisible();
  const label = (name) => page.getByLabel(name, { exact: true });
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
        if (!callback || await page.title() !== "Sign in to Codex with ChatGPT - OpenAI") return result("verification_required");
        if (!unchanged()) return result("stopped");
        submitted.add(path);
        await page.getByRole("button", { name: "Continue", exact: true }).click({ timeout: 3000, noWaitAfter: true });
        continue;
      }
      const field = path === "/log-in" ? "Email address" : path === "/log-in/password" ? "Password" : path.startsWith("/mfa-challenge/") ? "One-time code" : null;
      if (!field && !submitted.has(path) && !["/oauth/authorize", "/authorize"].includes(path)) return result("verification_required");
      const input = field === "Email address" && !await visible(label(field))
        ? page.locator('input[type="email"]') : field ? label(field) : null;
      if (field && input && !submitted.has(path) && await visible(input)) {
        if (field === "Password" && (await label("Email address").inputValue({ timeout: 3000 })).toLowerCase() !== credential.email.toLowerCase()) return result("account_mismatch");
        if (field === "One-time code" && !await visible(heading("Check your authenticator app"))) return result("verification_required");
        if (!unchanged()) return result("stopped");
        const value = field === "Email address" ? credential.email : field === "Password" ? credential.password : totp(credential.totpSecret, now());
        await input.fill(value, { timeout: 3000 });
        if (!unchanged()) return result("stopped");
        submitted.add(path);
        await page.getByRole("button", { name: "Continue", exact: true }).click({ timeout: 3000, noWaitAfter: true });
        continue;
      }
      await setTimeout(250, undefined, { signal });
    }
    return result("verification_required");
  } catch {
    return result(stopped() ? "stopped" : "verification_required");
  }
}

export async function verifyResumableAccount(page, credential) {
  try {
    const current = new URL(page.url());
    if (current.origin !== "https://auth.openai.com") return false;
    if (current.pathname === "/log-in/password") {
      const email = await page.getByLabel("Email address", { exact: true }).inputValue({ timeout: 3000 });
      return email.toLowerCase() === credential.email.toLowerCase();
    }
    if (current.pathname !== "/sign-in-with-chatgpt/codex/consent" && !current.pathname.startsWith("/mfa-challenge/")) return false;
    await page.getByText(credential.email, { exact: true }).waitFor({ state: "visible", timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}
