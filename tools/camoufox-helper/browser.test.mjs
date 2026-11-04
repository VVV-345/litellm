// 用真实 HTTP 上游验证本机转接的认证与失败封闭，不依赖外部 ISP。
import test from "node:test";
import assert from "node:assert/strict";
import { createServer, request } from "node:http";
import { startBridge } from "./browser.mjs";
const proxyRequest = (bridge, authenticated = true) =>
  new Promise((resolve, reject) => {
    const url = new URL(bridge.proxy.server);
    const headers = authenticated
      ? {
          "Proxy-Authorization":
            "Basic " +
            Buffer.from(`browser:${bridge.proxy.password}`).toString("base64"),
        }
      : {};
    const req = request(
      {
        hostname: url.hostname,
        port: url.port,
        path: "http://destination.invalid/verify",
        headers,
      },
      (response) => {
        const chunks = [];
        response.on("data", (data) => chunks.push(data));
        response.on("end", () =>
          resolve({
            status: response.statusCode,
            body: Buffer.concat(chunks).toString(),
          }),
        );
      },
    );
    req.on("error", reject);
    req.end();
  });
test("authenticates the upstream and returns an error when it is down instead of connecting directly", async (t) => {
  const upstream = createServer((request, response) => {
    assert.equal(
      request.headers["proxy-authorization"],
      "Basic " + Buffer.from("alice:p@ss").toString("base64"),
    );
    assert.equal(request.url, "http://destination.invalid/verify");
    response.end("through upstream");
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const bridge = await startBridge({
    proxy_type: "http",
    host: "127.0.0.1",
    port: upstream.address().port,
    username: "alice",
    password: "p@ss",
  });
  t.after(async () => {
    await bridge.close();
    upstream.closeAllConnections();
    upstream.close();
  });
  assert.equal((await proxyRequest(bridge, false)).status, 407);
  assert.deepEqual(await proxyRequest(bridge), {
    status: 200,
    body: "through upstream",
  });
  upstream.closeAllConnections();
  await new Promise((resolve) => upstream.close(resolve));
  const failed = await proxyRequest(bridge);
  assert.ok(failed.status >= 500);
  assert.equal(failed.body.includes("p@ss"), false);
});
