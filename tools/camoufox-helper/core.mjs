// 管理本机代理匹配与每卡浏览器会话；登记表不保存 ISP 凭据或 OAuth 链接。
import { createHmac, randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { automateLogin, parseCredential } from "./login.mjs";

export class HelperError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}
export const proxySchema = z
  .object({
    proxy_type: z.enum(["http", "https", "socks5"]),
    host: z
      .string()
      .min(1)
      .max(253)
      .regex(/^[a-zA-Z0-9.:[\]-]+$/),
    port: z.number().int().min(1).max(65535),
    username: z.string().max(1024),
    password: z.string().max(4096),
  })
  .strict();
export const prepareSchema = z
  .object({ card_id: z.string().uuid(), proxy: proxySchema })
  .strict();
export const openSchema = z
  .object({
    ticket: z.string().regex(/^[a-f0-9]{64}$/),
    url: z.string().max(8192),
  })
  .strict();
export const loginSchema = z.object({
  ticket: z.string().regex(/^[a-f0-9]{64}$/),
  credentials: z.string().min(1).max(8192),
  allow_consent: z.boolean().default(false),
}).strict();
const registrySchema = z
  .object({ proxies: z.array(z.string().regex(/^[a-f0-9]{64}$/)) })
  .strict();
const normalized = (proxy) => ({
  ...proxy,
  host: proxy.host
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, ""),
});
export function upstreamUrl(value) {
  const proxy = normalized(proxySchema.parse(value));
  if (proxy.proxy_type === "socks5" && proxy.host.includes(":"))
    throw new HelperError("proxy");
  const host = proxy.host.includes(":") ? `[${proxy.host}]` : proxy.host;
  const credentials =
    proxy.username || proxy.password
      ? `${encodeURIComponent(proxy.username)}:${encodeURIComponent(proxy.password)}@`
      : "";
  return `${proxy.proxy_type === "socks5" ? "socks5h" : proxy.proxy_type}://${credentials}${host}:${proxy.port}`;
}
export function validateAuthorization(value) {
  try {
    const url = new URL(value);
    const validHost =
      url.protocol === "https:" &&
      url.hostname === "auth.openai.com" &&
      !url.port;
    if (
      !validHost ||
      url.username ||
      url.password ||
      !["/oauth/authorize", "/authorize"].includes(url.pathname)
    )
      throw new Error();
    if ((url.searchParams.get("state") ?? "").length < 16) throw new Error();
    const callback = new URL(url.searchParams.get("redirect_uri"));
    const local =
      callback.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(callback.hostname);
    if (
      !local ||
      !callback.port ||
      callback.pathname !== "/auth/callback" ||
      callback.username ||
      callback.password ||
      callback.search ||
      callback.hash
    )
      throw new Error();
    return { url, callback };
  } catch {
    throw new HelperError("url");
  }
}

export class CamoufoxSessions {
  constructor({ root, key, launch, callbackPort = null, now = Date.now, login = automateLogin }) {
    this.root = root;
    this.key = key;
    this.launch = launch;
    this.automateLogin = login;
    this.callbackPort = callbackPort;
    this.now = now;
    this.active = new Map();
    this.pending = new Set();
    this.tickets = new Map();
    this.closing = false;
    this.jobs = new Set();
    mkdirSync(root, { recursive: true, mode: 0o700 });
    chmodSync(root, 0o700);
    this.registryPath = join(root, "registry.json");
    this.registry = existsSync(this.registryPath)
      ? registrySchema.parse(
          JSON.parse(readFileSync(this.registryPath, "utf8")),
        )
      : { proxies: [] };
  }
  fingerprint(value) {
    return createHmac("sha256", this.key)
      .update(JSON.stringify(value))
      .digest("hex");
  }
  save() {
    const temporary = this.registryPath + "." + randomBytes(8).toString("hex");
    writeFileSync(temporary, JSON.stringify(this.registry), {
      mode: 0o600,
      flag: "wx",
    });
    renameSync(temporary, this.registryPath);
  }
  async prepare(origin, input) {
    if (this.closing) throw new HelperError("busy");
    const job = this.prepareSession(origin, input);
    this.jobs.add(job);
    try {
      return await job;
    } finally {
      this.jobs.delete(job);
    }
  }
  async prepareSession(origin, input) {
    const request = prepareSchema.parse(input);
    const binding = this.fingerprint([origin, request.card_id]);
    const proxyId = this.fingerprint(normalized(request.proxy));
    if (this.pending.has(binding)) throw new HelperError("busy");
    this.pending.add(binding);
    try {
      const existing = this.active.get(binding);
      if (existing?.loginController || existing?.opening) throw new HelperError("busy");
      if (existing && existing.proxyId !== proxyId)
        throw new HelperError("running");
      const imported = !this.registry.proxies.includes(proxyId);
      const session = existing ?? {
        proxyId,
        context: await this.launch(
          join(this.root, "profiles", binding),
          request.proxy,
        ),
      };
      if (this.closing) {
        await session.context.close();
        throw new HelperError("busy");
      }
      if (!existing) {
        this.active.set(binding, session);
        session.context.on("close", () => {
          session.loginController?.abort();
          if (this.active.get(binding) === session) this.active.delete(binding);
          for (const [id, ticket] of this.tickets)
            if (ticket.session === session) this.tickets.delete(id);
        });
      }
      if (imported) {
        this.registry = { proxies: [...this.registry.proxies, proxyId] };
        this.save();
      }
      for (const [id, ticket] of this.tickets)
        if (ticket.expires <= this.now()) this.tickets.delete(id);
      const ticket = randomBytes(32).toString("hex");
      this.tickets.set(ticket, {
        session,
        origin,
        binding,
        expires: this.now() + 15 * 60_000,
        opening: false,
        opened: null,
      });
      return { ticket, proxy_id: proxyId, imported };
    } finally {
      this.pending.delete(binding);
    }
  }
  async open(origin, input) {
    if (this.closing) throw new HelperError("busy");
    const request = openSchema.parse(input);
    const ticket = this.tickets.get(request.ticket);
    if (
      !ticket ||
      ticket.origin !== origin ||
      ticket.expires <= this.now() ||
      this.active.get(ticket.binding) !== ticket.session
    )
      throw new HelperError("expired");
    const { url, callback } = validateAuthorization(request.url);
    if (ticket.opening || ticket.session.opening || ticket.session.loginController) throw new HelperError("busy");
    if (ticket.opened === request.url) return { opened: true };
    if (ticket.opened) throw new HelperError("expired");
    ticket.opening = true;
    ticket.session.opening = true;
    try {
      const page = await ticket.session.context.newPage();
      if (this.callbackPort && callback.port !== String(this.callbackPort)) {
        // 只改写当前授权的回环回调，避免本机旧实例占用默认端口时把 code 交给错误 Manager。
        await page.route(
          `${callback.origin}${callback.pathname}*`,
          async (route) => {
            const target = new URL(route.request().url());
            if (
              target.origin !== callback.origin ||
              target.pathname !== callback.pathname ||
              target.searchParams.get("state") !== url.searchParams.get("state")
            ) {
              await route.abort();
              return;
            }
            target.hostname = "127.0.0.1";
            target.port = String(this.callbackPort);
            await route.fulfill({
              status: 302,
              headers: {
                Location: target.toString(),
                "Cache-Control": "no-store",
              },
              body: "",
            });
          },
        );
      }
      await page.goto(request.url, { waitUntil: "commit", timeout: 45_000 });
      await page.bringToFront();
      ticket.opened = request.url;
      ticket.page = page;
      ticket.session.currentTicket = request.ticket;
      const target = new URL(callback);
      if (this.callbackPort && callback.port !== String(this.callbackPort)) {
        target.hostname = "127.0.0.1";
        target.port = String(this.callbackPort);
      }
      target.searchParams.set("state", url.searchParams.get("state"));
      ticket.callback = target.href;
      return { opened: true };
    } catch (error) {
      if (error instanceof HelperError) throw error;
      throw new HelperError("navigation");
    } finally {
      ticket.opening = false;
      ticket.session.opening = false;
    }
  }
  async login(origin, input, signal) {
    if (this.closing) throw new HelperError("busy");
    const request = loginSchema.parse(input);
    const ticket = this.tickets.get(request.ticket);
    if (!ticket || ticket.origin !== origin || ticket.expires <= this.now() ||
      this.active.get(ticket.binding) !== ticket.session || !ticket.page ||
      ticket.session.currentTicket !== request.ticket) throw new HelperError("expired");
    if (ticket.session.loginController || ticket.session.opening) throw new HelperError("busy");
    if (ticket.loginUsed) throw new HelperError("expired");
    let credential;
    try { credential = parseCredential(request.credentials); }
    catch { throw new HelperError("credential"); }
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    ticket.session.loginController = controller;
    ticket.loginUsed = true;
    const job = this.automateLogin(ticket.page, credential, {
      signal: controller.signal, callback: ticket.callback, allowConsent: request.allow_consent,
    });
    this.jobs.add(job);
    try { return await job; }
    catch { throw new HelperError("login"); }
    finally {
      signal?.removeEventListener("abort", abort);
      ticket.session.loginController = null;
      this.jobs.delete(job);
    }
  }
  async close() {
    this.closing = true;
    for (const session of this.active.values()) session.loginController?.abort();
    await Promise.allSettled([...this.jobs]);
    await Promise.allSettled(
      [...this.active.values()].map((session) => session.context.close()),
    );
  }
}
