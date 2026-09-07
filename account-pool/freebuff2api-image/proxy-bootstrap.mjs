/** 本模块在 FreeBuff 启动前设置 fetch 的公共代理，不接触授权凭据和模型业务。 */
import { ProxyAgent, setGlobalDispatcher } from "undici";

const proxyUrl = process.env.FREEBUFF_PROXY_URL?.trim();

if (proxyUrl) {
  const parsed = URL.parse(proxyUrl);
  if (
    !parsed ||
    !["http:", "https:"].includes(parsed.protocol) ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("FREEBUFF_PROXY_URL must be a credential-free HTTP(S) proxy origin");
  }
  // 代理失败时由 fetch 返回错误，禁止回退直连而绕过账号选定的出口。
  setGlobalDispatcher(new ProxyAgent(parsed.href));
}
