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
