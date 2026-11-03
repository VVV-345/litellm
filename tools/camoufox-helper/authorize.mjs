import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { HelperError, proxySchema, validateAuthorization } from "./core.mjs";
import { parseCredential } from "./login.mjs";

function origin(value, localOnly) {
  const url = new URL(value);
  const local = url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname) && !!url.port;
  if (url.origin !== value || (!local && (localOnly || url.protocol !== "https:"))) throw new HelperError("address");
  return url.origin;
}

export async function authorizeAccount(options, transport = fetch) {
  const dashboard = origin(options.dashboard, false);
  const helper = origin(options.helper, true);
  const card = z.string().uuid().parse(options.cardId);
  parseCredential(options.credentials);
  const request = async (base, path, body, key) => {
    try {
      const response = await transport(base + path, {
        method: "POST", headers: {
          Authorization: `Bearer ${key.trim()}`, "Content-Type": "application/json",
          ...(base === helper ? { Origin: dashboard } : {}),
          ...(path.endsWith("/authorize") ? { "Idempotency-Key": randomUUID() } : {}),
        }, body: JSON.stringify(body), redirect: "error", cache: "no-store",
        signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000),
      });
      if (!response.ok) throw new HelperError(`http_${response.status}`);
      return await response.json();
    } catch (error) {
      if (error instanceof HelperError) throw error;
      throw new HelperError(options.signal?.aborted ? "stopped" : "connection");
    }
  };
  const remote = (path, body = {}) => request(dashboard, `/account_pool/environments/${card}${path}`, body, options.accessToken);
  const local = (path, body) => request(helper, `/v1${path}`, body, options.helperKey);
  const proxy = proxySchema.parse(await remote("/browser-proxy"));
  const prepared = z.object({ ticket: z.string().regex(/^[a-f0-9]{64}$/) }).parse(await local("/prepare", { card_id: card, proxy }));
  const authorization = z.object({ flow: z.literal("browser_oauth"), authorization_url: z.string() }).parse(await remote("/authorize"));
  validateAuthorization(authorization.authorization_url);
  const currentProxy = proxySchema.parse(await remote("/browser-proxy"));
  if (JSON.stringify(currentProxy) !== JSON.stringify(proxy)) throw new HelperError("changed");
  z.object({ opened: z.literal(true) }).parse(await local("/open", { ticket: prepared.ticket, url: authorization.authorization_url }));
  const result = await local("/login", { ticket: prepared.ticket, credentials: options.credentials, allow_consent: options.allowConsent ?? true });
  return z.object({ status: z.enum(["confirmation_required", "callback_received", "verification_required", "account_mismatch", "stopped"]) }).parse(result);
}

async function main() {
  try {
    const { values } = parseArgs({ options: {
      dashboard: { type: "string", default: "http://127.0.0.1:4001" },
      helper: { type: "string", default: "http://127.0.0.1:10109" },
      "card-id": { type: "string" }, "credentials-file": { type: "string" },
      "access-token-file": { type: "string" },
      "helper-key-file": { type: "string", default: join(homedir(), ".litellm-camoufox", "helper.key") },
      "allow-consent": { type: "boolean", default: true },
    } });
    const controller = new AbortController();
    const stop = () => controller.abort();
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
    try {
      const result = await authorizeAccount({
        dashboard: values.dashboard, helper: values.helper, cardId: values["card-id"],
        credentials: await readFile(values["credentials-file"], "utf8"),
        accessToken: await readFile(values["access-token-file"], "utf8"),
        helperKey: await readFile(values["helper-key-file"], "utf8"),
        allowConsent: values["allow-consent"], signal: controller.signal,
      });
      console.log(JSON.stringify(result));
      if (result.status !== "callback_received") process.exitCode = 2;
    } finally {
      process.removeListener("SIGINT", stop);
      process.removeListener("SIGTERM", stop);
    }
  } catch (error) {
    console.error(JSON.stringify({ code: error instanceof HelperError ? error.code : "configuration_or_response_invalid" }));
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
