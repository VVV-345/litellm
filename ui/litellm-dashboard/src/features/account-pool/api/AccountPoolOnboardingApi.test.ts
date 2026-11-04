/** 验证邮箱导入不会混淆两类密码或接受错误格式。 */
import { describe, expect, it, vi } from "vitest";
vi.mock("@/components/networking", () => ({ apiClient: {} }));
import { parseMailboxAccounts } from "./AccountPoolOnboardingApi";

describe("parseMailboxAccounts", () => {
  it.each(["\ufeff", "卡密导出\n\n", "\ufeff \r\n 卡密导出 \r\n\r\n"])(
    "recognizes two-factor lines with prefix %j without mixing passwords",
    (prefix) => {
      expect(
        parseMailboxAccounts(
          prefix +
            "first@example.test----fixture-password----jbswy3dpehpk3pxp\r\nsecond@example.test----other----JBSWY3DPEHPK3PXP",
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
    },
  );

  it.each(["invalid-account", "卡密导出"])("does not skip an invalid account line %j", (line) => {
    expect(() =>
      parseMailboxAccounts("卡密导出\n\nfixture@example.test----password----JBSWY3DPEHPK3PXP\n" + line),
    ).toThrow("2FA 格式或密钥无效");
  });

  it("counts accounts rather than the export header for the batch limit", () => {
    const account = "fixture@example.test----password----JBSWY3DPEHPK3PXP";
    expect(parseMailboxAccounts("卡密导出\n\n" + Array(100).fill(account).join("\n"))).toHaveLength(100);
    expect(() => parseMailboxAccounts("卡密导出\n\n" + Array(101).fill(account).join("\n"))).toThrow();
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
