/** 验证邮箱导入不会混淆两类密码或接受错误格式。 */
import { describe, expect, it, vi } from "vitest";
vi.mock("@/components/networking", () => ({ apiClient: {} }));
import { parseMailboxAccounts } from "./AccountPoolOnboardingApi";

describe("parseMailboxAccounts", () => {
  it("preserves separate mailbox and supplier passwords", () => {
    expect(
      parseMailboxAccounts('[{"email":" a@example.com ","mailbox_password":"mail","supplier_password":"provider"}]'),
    ).toEqual([{ label: "a@example.com", content: "", mailbox_password: "mail", supplier_password: "provider" }]);
  });
  it("does not infer a supplier password from the mailbox password", () => {
    expect(parseMailboxAccounts('[{"email":"a@example.com","mailbox_password":"mail"}]')[0].supplier_password).toBe("");
  });
  it.each(["{}", "[]", '[{"email":"a@example.com","password":"ambiguous"}]', "[null]"])(
    "rejects malformed input %s",
    (input) => {
      expect(() => parseMailboxAccounts(input)).toThrow();
    },
  );
});
