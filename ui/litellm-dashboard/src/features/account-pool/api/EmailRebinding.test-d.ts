/** 验证生成契约要求显式确认，公开邮箱视图不暴露收信秘密。 */
import { expectTypeOf, test } from "vitest";
import type { components } from "@/lib/http/schema";
import type { BindingPoolView, BindingView } from "./AccountPoolEmailRebindingApi";

test("confirmation requires the literal true flag", () => {
  expectTypeOf<components["schemas"]["ConfirmBinding"]>().toMatchObjectType<{ version: number; confirmed: true }>();
});
test("binding results include persisted external-change state", () => {
  expectTypeOf<BindingView>().toHaveProperty("email_changed").toEqualTypeOf<boolean>();
});
test("public mailbox records exclude password and refresh tokens", () => {
  expectTypeOf<BindingPoolView["mailboxes"][number]>().not.toHaveProperty("password");
  expectTypeOf<BindingPoolView["mailboxes"][number]>().not.toHaveProperty("refresh_token");
});
