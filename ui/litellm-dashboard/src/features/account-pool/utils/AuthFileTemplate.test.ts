/** 验证表单模板只改显式字段，不改变账号身份、令牌或未选配置。 */
import { describe, expect, it } from "vitest";
import { authFileTemplateFromForm, authFileTemplateToForm, mergeAuthFileTemplate } from "./AuthFileTemplate";

describe("auth-file form templates", () => {
  it("keeps zero priority and empty prefix explicit without sending unselected fields", () => {
    const form = authFileTemplateToForm({ priority: 0, prefix: "", proxy_profile_id: null });
    expect(authFileTemplateFromForm(form)).toEqual({ priority: 0, prefix: "", proxy_profile_id: null });
  });

  it.each(["1.5", "2147483648", "abc", "", "Infinity"])("rejects invalid priority %s", (priority) => {
    expect(() => authFileTemplateFromForm({ ...authFileTemplateToForm({ priority: 1 }), priority })).toThrow(
      "优先级必须是 32 位整数",
    );
  });

  it("merges only priority/prefix, preserving identity, tokens and unrelated JSON fields", () => {
    const original = {
      email: "account@example.test",
      refresh_token: "private-fixture",
      access_token: "fixture-access",
      proxy_url: "http://existing:80",
      priority: 9,
      nested: { keep: true },
    };
    const merged = JSON.parse(
      mergeAuthFileTemplate(JSON.stringify(original), { priority: 0, prefix: "team", proxy_profile_id: "node-a" }),
    );
    expect(merged).toEqual({ ...original, priority: 0, prefix: "team" });
  });

  it("clears an embedded proxy only when following the card proxy is explicitly selected", () => {
    expect(
      JSON.parse(
        mergeAuthFileTemplate('{"proxy_url":"http://old:80","refresh_token":"fixture"}', { proxy_profile_id: "" }),
      ),
    ).toEqual({ proxy_url: "", refresh_token: "fixture" });
  });

  it.each(["[]", "null", '{"access_token":"secret",'])(
    "rejects invalid auth JSON without echoing credential contents",
    (content) => {
      expect(() => mergeAuthFileTemplate(content, { priority: 1 })).toThrow(/^认证文件必须是有效的 JSON 对象$/);
    },
  );
});
