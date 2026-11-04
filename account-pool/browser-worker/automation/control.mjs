// 管理单个临时浏览器的脚本与人工输入互斥，不持久化凭据或 Cookie。
import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";

export class BrowserControl {
  constructor({ login, enableInput, verifyAccount, close, totalDeadline, humanMs, now = Date.now }) {
    this.login = login;
    this.enableInput = enableInput;
    this.verifyAccount = verifyAccount;
    this.close = close;
    this.totalDeadline = totalDeadline;
    this.humanMs = humanMs;
    this.now = now;
    this.status = "prepared";
    this.reason = null;
    this.humanDeadline = null;
    this.abort = null;
    this.pending = Promise.resolve();
    this.commands = Promise.resolve();
  }

  command(action) {
    const result = this.commands.then(() => this.execute(action));
    this.commands = result.catch(() => {});
    return result;
  }

  view() {
    return {
      status: this.status, reason: this.reason,
      expires_at: new Date(Math.min(this.totalDeadline, this.humanDeadline ?? Infinity)).toISOString(),
    };
  }

  async execute(action) {
    if (!["prepared", "cancelled", "expired", "failed", "callback_received"].includes(this.status) &&
        this.now() >= Math.min(this.totalDeadline, this.humanDeadline ?? Infinity)) {
      await this.stop("expired");
    }
    if (action === "status") return this.view();
    if (["cancelled", "expired", "failed", "callback_received"].includes(this.status)) return this.view();
    if (this.now() >= this.totalDeadline) {
      await this.stop("expired");
      return this.view();
    }
    if (action === "cancel") await this.stop("cancelled");
    else if (action === "start" && this.status === "prepared") await this.run();
    else if (action === "takeover" && ["running", "waiting", "manual"].includes(this.status)) {
      this.abort?.abort();
      await this.pending;
      if (["callback_received", "failed"].includes(this.status)) return this.view();
      this.wait("manual", "manual_takeover");
      await this.enableInput(true);
    } else if (action === "resume" && ["manual", "waiting"].includes(this.status)) {
      await this.enableInput(false);
      if (await this.verifyAccount()) {
        this.humanDeadline = null;
        await this.run();
      } else this.wait("waiting", "account_verification_required");
    }
    return this.view();
  }

  wait(status, reason) {
    this.status = status;
    this.reason = reason;
    this.humanDeadline ??= Math.min(this.totalDeadline, this.now() + this.humanMs);
  }

  async run() {
    await this.enableInput(false);
    this.status = "running";
    this.reason = null;
    this.abort = new AbortController();
    this.pending = this.login(this.abort.signal).then((result) => {
      if (result.status === "stopped") return;
      if (result.status === "callback_received") this.status = "callback_received";
      else this.wait("waiting", result.status);
    }).catch(() => {
      this.status = "failed";
      this.reason = "browser_driver_failed";
    });
  }

  async stop(status) {
    this.abort?.abort();
    await this.pending;
    await this.enableInput(false);
    await this.close();
    this.status = status;
    this.reason = status;
  }
}

export function createControlServer({ token, createController }) {
  if (typeof token !== "string" || token.length < 32) throw new Error("job_token");
  const expected = Buffer.from(`Bearer ${token}`);
  let controller = null;
  const server = createServer(async (request, response) => {
    const reply = (status, value) => {
      response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify(value));
    };
    const supplied = Buffer.from(request.headers.authorization ?? "");
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      reply(401, { error: "unauthorized" });
      return;
    }
    if (request.method !== "POST" || request.url !== "/control") {
      reply(404, { error: "not_found" });
      return;
    }
    try {
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 16_384) { reply(413, { error: "request_too_large" }); return; }
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!body || Array.isArray(body) || typeof body !== "object" ||
          !["start", "status", "takeover", "resume", "cancel"].includes(body.action) ||
          Object.keys(body).some((key) => key !== "action" && !(key === "credentials" && body.action === "start"))) {
        reply(400, { error: "invalid_command" });
        return;
      }
      if (body.action === "start" && controller === null) {
        if (typeof body.credentials !== "string" || Buffer.byteLength(body.credentials) > 8192) {
          reply(400, { error: "invalid_credentials" });
          return;
        }
        controller = Promise.resolve().then(() => createController(body.credentials));
      }
      if (controller === null) { reply(409, { error: "not_started" }); return; }
      reply(200, await (await controller).command(body.action));
    } catch {
      reply(400, { error: "job_command_failed" });
    }
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 5000;
  return server;
}
