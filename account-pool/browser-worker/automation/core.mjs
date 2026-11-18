// 从 PR #4 复用 Codex 授权地址校验；只接受 Manager 生成的受信域和回调。
export function validateAuthorization(value) {
  try {
    if (typeof value !== "string" || value.length > 8192) throw new Error();
    const url = new URL(value);
    if (
      url.protocol !== "https:" || url.hostname !== "auth.openai.com" || url.port ||
      url.username || url.password || url.hash ||
      !["/oauth/authorize", "/authorize"].includes(url.pathname) ||
      url.searchParams.getAll("state").length !== 1 ||
      url.searchParams.getAll("redirect_uri").length !== 1
    ) throw new Error();
    const state = url.searchParams.get("state");
    if (state.length < 16 || state.length > 512) throw new Error();
    const callback = new URL(url.searchParams.get("redirect_uri"));
    if (
      callback.protocol !== "http:" ||
      !["localhost", "127.0.0.1", "[::1]"].includes(callback.hostname) ||
      !callback.port || callback.pathname !== "/auth/callback" ||
      callback.username || callback.password || callback.search || callback.hash
    ) throw new Error();
    return { url, callback };
  } catch {
    throw new Error("url");
  }
}

export async function runStartupStage(stage, operation, {
  now = Date.now,
  log = (event) => process.stderr.write(`oauth_browser_startup ${JSON.stringify(event)}\n`),
} = {}) {
  const started = now();
  log({ stage, status: "started", elapsed_ms: 0 });
  try {
    const result = await operation();
    log({ stage, status: "ready", elapsed_ms: Math.max(0, now() - started) });
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    const reason = error?.name === "TimeoutError" ? "timeout"
      : /proxy/i.test(message) ? "proxy_error"
      : /SSL|TLS|CERT_/i.test(message) ? "tls_error" : "process_error";
    log({ stage, status: "failed", elapsed_ms: Math.max(0, now() - started), reason });
    throw error;
  }
}

export async function openAuthorizationPage(page, authorization, seconds = "60", diagnostics) {
  const timeout = Number(seconds) * 1000;
  if (!Number.isInteger(timeout) || timeout <= 0 || timeout > 120_000) throw new Error("invalid navigation timeout");
  // Cloudflare 挑战页会长时间不触发 domcontentloaded，只等响应头落地即可交回控制层处理挑战。
  return runStartupStage("page_navigation", () => page.goto(authorization, {
    waitUntil: "commit", timeout,
  }), diagnostics);
}
