/** 本文件通过真实 HTTP 隧道验证 Node fetch 的代理路由、流式响应及失败行为。 */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { connect } from "node:net";
import { promisify } from "node:util";
import test from "node:test";

const exec = promisify(execFile);
const bootstrap = new URL("./proxy-bootstrap.mjs", import.meta.url).href;

async function listen(server, context) {
  const sockets = new Set();
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  context.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
  });
  return server.address().port;
}

function fetchInChild(url, proxyUrl) {
  return exec(process.execPath, ["--import", bootstrap, "--input-type=module", "-e", `
    const response = await fetch(process.env.TEST_TARGET, { signal: AbortSignal.timeout(3000) });
    if (response.status !== 200) throw new Error("Unexpected status");
    for await (const chunk of response.body) process.stdout.write(chunk);
  `], {
    env: { ...process.env, FREEBUFF_PROXY_URL: proxyUrl, TEST_TARGET: url },
    timeout: 8000,
  });
}

test("routes native fetch and streaming data through the selected proxy", async (context) => {
  const origin = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    response.write("data: first\n\n");
    setTimeout(() => response.end("data: second\n\n"), 30);
  });
  const originPort = await listen(origin, context);
  const tunnels = [];
  const proxy = createServer();
  proxy.on("connect", (request, socket, head) => {
    tunnels.push(request.url);
    const upstream = connect(originPort, "127.0.0.1", () => {
      socket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    socket.on("close", () => upstream.destroy());
    upstream.on("error", () => socket.destroy());
  });
  const proxyPort = await listen(proxy, context);

  // 目标域名无法直接解析，只有代理转发才可能成功。
  const result = await fetchInChild("http://freebuff-test.invalid/events", `http://127.0.0.1:${proxyPort}`);
  assert.equal(result.stdout, "data: first\n\ndata: second\n\n");
  assert.deepEqual(tunnels, ["freebuff-test.invalid:80"]);
});

test("empty configuration keeps direct fetch; failed proxy never falls back to direct", async (context) => {
  const requests = [];
  const origin = createServer((request, response) => {
    requests.push(request.url);
    response.end("direct");
  });
  const originPort = await listen(origin, context);
  const proxy = createServer();
  proxy.on("connect", (_request, socket) => socket.end("HTTP/1.1 403 Forbidden\r\n\r\n"));
  const proxyPort = await listen(proxy, context);
  const target = `http://127.0.0.1:${originPort}/test`;

  assert.equal((await fetchInChild(target, "")).stdout, "direct");
  await assert.rejects(fetchInChild(target, `http://127.0.0.1:${proxyPort}`));
  assert.deepEqual(requests, ["/test"]);
});

test("invalid proxy configuration fails at startup without echoing credentials", async () => {
  for (const proxyUrl of ["invalid", "socks5://localhost:1080", "http://user:private-secret@localhost:7891"]) {
    await assert.rejects(fetchInChild("http://127.0.0.1/", proxyUrl), (error) => {
      assert.match(error.stderr, /FREEBUFF_PROXY_URL must be/);
      assert.doesNotMatch(error.stderr, /private-secret/);
      return true;
    });
  }
});
