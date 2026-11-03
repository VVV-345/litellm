/** 验证本地回调解析与当前授权绑定，不访问用户提供的地址。 */
import { describe, expect, it } from "vitest";

import { parseOAuthCallback } from "./oauthCallback";

const authorization =
  "https://auth.example/authorize?state=current&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback";
const callback = "http://localhost:1455/auth/callback?state=current&code=fixture-code";

describe("parseOAuthCallback", () => {
  it("extracts only the result and matching state", () => {
    expect(parseOAuthCallback(`${callback}&scope=ignored`, authorization)).toEqual({
      state: "current",
      code: "fixture-code",
    });
    expect(
      parseOAuthCallback(
        callback.replace("code=fixture-code", "error=access_denied&error_description=Cancelled"),
        authorization,
      ),
    ).toEqual({ state: "current", error: "access_denied", error_description: "Cancelled" });
  });

  it.each([
    callback.replace("state=current", "state=other"),
    callback.replace("localhost", "attacker.example"),
    callback.replace("/auth/callback", "/other"),
    `${callback}&state=current`,
    `${callback}&code=another`,
    `${callback}&error=denied`,
    `${callback}#code=hidden`,
    callback.replace("code=fixture-code", "code="),
    callback.replace("code=fixture-code", "error=denied&error=denied"),
    callback.replace("code=fixture-code", "error=denied&error_description=a&error_description=b"),
    "javascript:alert(1)",
    "invalid",
  ])("rejects a mismatched or ambiguous callback without echoing it", (value) => {
    expect(() => parseOAuthCallback(value, authorization)).toThrow("Invalid OAuth callback");
  });

  it("requires one redirect URI and one state in the authorization URL", () => {
    expect(() => parseOAuthCallback(callback, "https://auth.example/authorize?state=current")).toThrow(
      "Invalid OAuth callback",
    );
    expect(() => parseOAuthCallback(callback, `${authorization}&state=current`)).toThrow("Invalid OAuth callback");
  });
});
