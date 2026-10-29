/** 验证邮箱导入不会混淆两类密码或接受错误格式。 */
import { describe, expect, it, vi } from "vitest";
vi.mock("@/components/networking", () => ({ apiClient: {} }));
import { parseMailboxAccounts } from "./AccountPoolOnboardingApi";

describe("parseMailboxAccounts", () => {
  it("recognizes two-factor lines without treating the supplier password as a mailbox password", () => {
    expect(
      parseMailboxAccounts(
        "\ufefffirst@example.test----fixture-password----jbswy3dpehpk3pxp\r\nsecond@example.test----other----JBSWY3DPEHPK3PXP",
      ),
    ).toEqual([
      {
        label: "first@example.test",
        content: "",
        mailbox_password: "",
        supplier_password: "fixture-password",
        totp_secret: "JBSWY3DPEHPK3PXP",
      },
      {
        label: "second@example.test",
        content: "",
        mailbox_password: "",
        supplier_password: "other",
        totp_secret: "JBSWY3DPEHPK3PXP",
      },
    ]);
  });

  it("rejects malformed two-factor input without exposing it in the error", () => {
    expect(() => parseMailboxAccounts("fixture@example.test----secret-password----invalid-key")).toThrow(
      "2FA 格式或密钥无效",
    );
  });
  it("preserves separate mailbox and supplier passwords", () => {
    expect(
      parseMailboxAccounts('[{"email":" a@example.com ","mailbox_password":"mail","supplier_password":"provider"}]'),
    ).toEqual([
      { label: "a@example.com", content: "", mailbox_password: "mail", supplier_password: "provider", totp_secret: "" },
    ]);
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
