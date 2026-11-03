/** 仅解析手动回填的 OAuth 地址，并绑定当前授权的回调地址和 state。 */
import type { components } from "@/lib/http/schema";

export function parseOAuthCallback(
  callbackUrl: string,
  authorizationUrl: string,
): components["schemas"]["AccountPoolOAuthCallback"] {
  try {
    const authorization = new URL(authorizationUrl);
    const redirectUri = authorization.searchParams.get("redirect_uri");
    const state = authorization.searchParams.get("state");
    const uniqueAuthorization = ["state", "redirect_uri"].every(
      (field) => authorization.searchParams.getAll(field).length === 1,
    );
    if (!redirectUri || !state || !uniqueAuthorization) throw new Error();
    const expected = new URL(redirectUri);
    const callback = new URL(callbackUrl.trim());
    const unsafeAddress =
      !["http:", "https:"].includes(callback.protocol) ||
      [callback.username, callback.password, callback.hash].some(Boolean);
    const matchesAuthorization =
      callback.origin === expected.origin &&
      callback.pathname === expected.pathname &&
      callback.searchParams.get("state") === state;
    if (unsafeAddress || !matchesAuthorization) throw new Error();
    for (const field of ["state", "code", "error", "error_description"]) {
      if (callback.searchParams.getAll(field).length > 1) throw new Error();
    }
    const code = callback.searchParams.get("code");
    const error = callback.searchParams.get("error");
    const description = callback.searchParams.get("error_description");
    if (code?.trim() && error === null && description === null) return { state, code };
    if (error?.trim() && code === null)
      return { state, error, ...(description ? { error_description: description } : {}) };
    throw new Error();
  } catch {
    throw new Error("Invalid OAuth callback");
  }
}
