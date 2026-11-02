// 验证本机连接助手的来源、Host、密钥和请求边界。
import test from "node:test";
import assert from "node:assert/strict";
import { request as httpRequest } from "node:http";
import { createHelperServer } from "./server.mjs";

test("requires the configured origin, loopback Host, and helper key before any action", async (t) => {
  const calls = [];
  const sessions = {
    prepare: async (...args) => {
      calls.push(args);
      return {
        ticket: "a".repeat(64),
        proxy_id: "b".repeat(64),
        imported: true,
      };
    },
  };
  const server = createHelperServer({
    origins: ["http://127.0.0.1:4001"],
    key: "private-helper-key",
    sessions,
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const payload = {
    card_id: "e5304d0c-462d-46c5-a738-7c3fc31de8ec",
    proxy: {
      proxy_type: "socks5",
      host: "isp.example",
      port: 1080,
      username: "user",
      password: "secret",
    },
  };
  for (const headers of [
    {
      Origin: "https://evil.example",
      Authorization: "Bearer private-helper-key",
    },
    { Origin: "http://127.0.0.1:4001", Authorization: "Bearer wrong" },
  ]) {
    const response = await fetch(base + "/v1/prepare", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    assert.equal(response.status, 403);
    assert.equal((await response.text()).includes("secret"), false);
  }
  const hostileHost = await new Promise((resolve) => {
    const request = httpRequest(
      base + "/v1/prepare",
      {
        method: "POST",
        headers: {
          Origin: "http://127.0.0.1:4001",
          Authorization: "Bearer private-helper-key",
          Host: "evil.example",
        },
      },
      (response) => {
        response.resume();
        resolve(response.statusCode);
      },
    );
    request.end(JSON.stringify(payload));
  });
  assert.equal(hostileHost, 403);
  assert.equal(calls.length, 0);
  const headers = {
    Origin: "http://127.0.0.1:4001",
    Authorization: "Bearer private-helper-key",
    "Content-Type": "application/json",
  };
  const ok = await fetch(base + "/v1/prepare", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get("Cache-Control"), "no-store");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "http://127.0.0.1:4001");
  const invalid = await fetch(base + "/v1/prepare", {
    method: "POST",
    headers,
    body: JSON.stringify({ ...payload, card_id: "../../secrets" }),
  });
  assert.equal(invalid.status, 400);
  assert.equal(calls.length, 1);
  const preflight = await fetch(base + "/v1/prepare", {
    method: "OPTIONS",
    headers: {
      Origin: headers.Origin,
      "Access-Control-Request-Method": "POST",
    },
  });
  assert.equal(preflight.status, 204);
  assert.equal(
    preflight.headers.get("Access-Control-Allow-Origin"),
    headers.Origin,
  );
});
