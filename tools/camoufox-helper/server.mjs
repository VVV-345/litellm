// 仅向明确允许的网页来源暴露回环 API，禁止请求控制浏览器路径或执行命令。
import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { HelperError, prepareSchema, openSchema, loginSchema } from "./core.mjs";

const sameKey = (provided, expected) => {
  const a = Buffer.from(provided ?? "");
  const b = Buffer.from(`Bearer ${expected}`);
  return a.length === b.length && timingSafeEqual(a, b);
};
const statuses = {
  busy: 409,
  running: 409,
  expired: 409,
  url: 400,
  launch: 502,
  navigation: 502,
  login: 502,
  credential: 400,
};
export function createHelperServer({ origins, key, sessions }) {
  const allowed = new Set(origins);
  return createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("Content-Type", "application/json");
    const fail = (status, code) => {
      response.statusCode = status;
      response.end(JSON.stringify({ code }));
    };
    const origin = request.headers.origin;
    const host = request.headers.host ?? "";
    const port = request.socket.localPort;
    if (
      !allowed.has(origin) ||
      ![`127.0.0.1:${port}`, `localhost:${port}`].includes(host)
    ) {
      fail(403, "unauthorized");
      return;
    }
    response.setHeader("Access-Control-Allow-Origin", origin);
    response.setHeader("Vary", "Origin");
    if (request.method === "OPTIONS") {
      response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      response.setHeader(
        "Access-Control-Allow-Headers",
        "Authorization, Content-Type",
      );
      response.setHeader("Access-Control-Allow-Private-Network", "true");
      response.statusCode = 204;
      response.end();
      return;
    }
    if (!sameKey(request.headers.authorization, key)) {
      fail(403, "unauthorized");
      return;
    }
    if (request.method === "GET" && request.url === "/v1/health") {
      response.end(
        JSON.stringify({ name: "litellm-camoufox-helper", version: 1 }),
      );
      return;
    }
    if (
      request.method !== "POST" ||
      !["/v1/prepare", "/v1/open", "/v1/login"].includes(request.url)
    ) {
      fail(404, "request");
      return;
    }
    if (!request.headers["content-type"]?.startsWith("application/json")) {
      fail(400, "request");
      return;
    }
    try {
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 16_384) {
          fail(413, "request");
          return;
        }
        chunks.push(chunk);
      }
      const schema = request.url === "/v1/prepare" ? prepareSchema : request.url === "/v1/login" ? loginSchema : openSchema;
      const input = schema.safeParse(
        JSON.parse(Buffer.concat(chunks).toString("utf8")),
      );
      if (!input.success) {
        fail(400, "request");
        return;
      }
      const controller = new AbortController();
      const cancel = () => controller.abort();
      response.once("close", cancel);
      try {
        const result = request.url === "/v1/login"
          ? await sessions.login(origin, input.data, controller.signal)
          : request.url === "/v1/prepare" ? await sessions.prepare(origin, input.data) : await sessions.open(origin, input.data);
        response.end(JSON.stringify(result));
      } finally {
        response.removeListener("close", cancel);
      }
    } catch (error) {
      const code = error instanceof HelperError ? error.code : "request";
      fail(statuses[code] ?? 400, code);
    }
  });
}
