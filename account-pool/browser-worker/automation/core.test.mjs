// 验证 PR #4 受信授权地址边界，禁止重定向和重复 state 混淆。
import assert from "node:assert/strict";
import test from "node:test";
import { validateAuthorization } from "./core.mjs";

const valid = () => {
  const url = new URL("https://auth.openai.com/oauth/authorize");
  url.searchParams.set("state", "one-time-state-0123456789");
  url.searchParams.set("redirect_uri", "http://localhost:1455/auth/callback");
  return url;
};

test("server-generated Codex authorization retains exact loopback callback and state", () => {
  const result = validateAuthorization(valid().href);
  assert.equal(result.callback.href, "http://localhost:1455/auth/callback");
  assert.equal(result.url.searchParams.get("state"), "one-time-state-0123456789");
});

test("untrusted authorization cannot receive credentials", () => {
  for (const change of [
    (url) => { url.hostname = "auth.openai.com.example.org"; },
    (url) => { url.username = "someone"; },
    (url) => { url.hash = "#state"; },
    (url) => { url.searchParams.append("state", "another-state-0123456789"); },
    (url) => { url.searchParams.append("redirect_uri", "http://localhost:1456/auth/callback"); },
    (url) => { url.searchParams.set("redirect_uri", "https://example.org/auth/callback"); },
    (url) => { url.searchParams.set("redirect_uri", "http://localhost:1455/auth/callback?state=other"); },
    (url) => { url.searchParams.set("state", "x".repeat(513)); },
  ]) {
    const url = valid();
    change(url);
    assert.throws(() => validateAuthorization(url.href), /url/);
  }
});
