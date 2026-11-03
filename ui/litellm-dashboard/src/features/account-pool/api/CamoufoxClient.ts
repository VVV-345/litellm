/** 仅访问使用者本机 Camoufox 助手，连接 Key 不经过项目服务器。 */
import { z } from "zod";
import type { components } from "@/lib/http/schema";
export type BrowserProxy = components["schemas"]["AccountPoolBrowserProxy"];
export type CamoufoxErrorCode =
  | "proxy"
  | "address"
  | "key"
  | "connection"
  | "unauthorized"
  | "busy"
  | "running"
  | "expired"
  | "url"
  | "launch"
  | "navigation"
  | "request"
  | "invalid"
  | "changed"
  | "credential"
  | "login"
  | "unsupported";
export type CamoufoxLoginStatus = "confirmation_required" | "callback_received" | "verification_required" | "account_mismatch" | "stopped";
const errorCode = z.enum([
  "proxy",
  "unauthorized",
  "busy",
  "running",
  "expired",
  "url",
  "launch",
  "navigation",
  "request",
  "credential",
  "login",
]);
export class CamoufoxError extends Error {
  constructor(public readonly code: CamoufoxErrorCode) {
    super(code);
  }
}
export function sameProxy(a: BrowserProxy, b: BrowserProxy): boolean {
  const host = (value: string) =>
    value
      .toLowerCase()
      .replace(/^\[|\]$/g, "")
      .replace(/\.$/, "");
  const endpoint = a.proxy_type === b.proxy_type && host(a.host) === host(b.host) && a.port === b.port;
  return endpoint && a.username === b.username && a.password === b.password;
}
function localBaseUrl(address: string): string {
  try {
    const url = new URL(address);
    const local = url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname) && !!url.port;
    const credentials = url.username || url.password;
    const suffix = url.pathname !== "/" || url.search || url.hash;
    if (!local || credentials || suffix) throw new Error();
    return url.origin;
  } catch {
    throw new CamoufoxError("address");
  }
}
export class CamoufoxClient {
  readonly baseUrl: string;
  constructor(
    address: string,
    private readonly key: string,
    private readonly transport: typeof fetch = fetch,
  ) {
    this.baseUrl = localBaseUrl(address);
    if (!key.trim() || /[\r\n]/.test(key)) throw new CamoufoxError("key");
  }
  private async request<T>(path: string, schema: z.ZodType<T>, body?: object, signal?: AbortSignal): Promise<T> {
    try {
      const options: RequestInit = {
        method: body ? "POST" : "GET",
        headers: { Authorization: `Bearer ${this.key.trim()}`, "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        referrerPolicy: "no-referrer",
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000),
      };
      const response = await this.transport(`${this.baseUrl}/v1${path}`, options);
      if (!response.ok) {
        const result = z.object({ code: errorCode }).safeParse(await response.json());
        throw new CamoufoxError(result.success ? result.data.code : "request");
      }
      const result = schema.safeParse(await response.json());
      if (!result.success) throw new CamoufoxError("invalid");
      return result.data;
    } catch (error) {
      if (error instanceof CamoufoxError) throw error;
      throw new CamoufoxError("connection");
    }
  }
  async testConnection(): Promise<void> {
    await this.request("/health", z.object({ name: z.literal("litellm-camoufox-helper"), version: z.literal(1) }));
  }
  prepare(proxy: BrowserProxy, cardId: string): Promise<{ ticket: string; proxy_id: string; imported: boolean }> {
    const shape = {
      ticket: z.string().regex(/^[a-f0-9]{64}$/),
      proxy_id: z.string().regex(/^[a-f0-9]{64}$/),
      imported: z.boolean(),
    };
    return this.request("/prepare", z.object(shape), { card_id: cardId, proxy });
  }
  async open(ticket: string, url: string): Promise<void> {
    await this.request("/open", z.object({ opened: z.literal(true) }), { ticket, url });
  }
  login(ticket: string, credentials: string, signal: AbortSignal): Promise<{ status: CamoufoxLoginStatus }> {
    const status = z.enum(["confirmation_required", "callback_received", "verification_required", "account_mismatch", "stopped"]);
    return this.request("/login", z.object({ status }), { ticket, credentials, allow_consent: true }, signal);
  }
}
