import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/http/client";
import { safeAutomationOperationMessage, saveAutomationSettings } from "./AccountPoolAutomationApi";

const { put } = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock("@/components/networking", () => ({ apiClient: { put }, serverRootPath: "" }));
beforeEach(() => vi.clearAllMocks());

it.each([422, 502])("shows audited %s errors without trusting arbitrary response bodies", (status) => {
  expect(safeAutomationOperationMessage(new ApiError("ignored", status, {
    detail: { code: "invalid_credentials", message: "登录资料格式错误" },
  }))).toBe("登录资料格式错误");
  for (const detail of ["private-token", { code: "unknown", message: "private-token" },
    { code: "invalid_credentials", message: "private-token".repeat(100) }]) {
    expect(safeAutomationOperationMessage(new ApiError("private-token", status, { detail }))).not.toContain("private-token");
  }
});

it.each([401, 403])("uses fixed operator instructions for status %s", (status) => {
  expect(safeAutomationOperationMessage(new ApiError("private-token", status, {}))).not.toContain("private-token");
});

it("compares the exact displayed settings while old clients can omit the snapshot", async () => {
  const original = { enabled: false, total_timeout_seconds: 600, human_timeout_seconds: 300 };
  const updated = { ...original, enabled: true };
  await saveAutomationSettings("test-admin", 1, updated, original);
  expect(put).toHaveBeenLastCalledWith("/account_pool/oauth-automation/settings", {
    accessToken: "test-admin", body: { version: 1, settings: updated, expected_settings: original },
  });
  await saveAutomationSettings("test-admin", 2, updated);
  expect(put).toHaveBeenLastCalledWith("/account_pool/oauth-automation/settings", {
    accessToken: "test-admin", body: { version: 2, settings: updated },
  });
});


it.each([
  ["proxy_location_failed", "代理地区、语言或时区匹配失败；未绑定指纹或启动登录，请检查出口及固定镜像"],
  ["profile_generation_failed", "指纹生成或验证失败，请先自检固定镜像"],
  ["profile_size_exceeded", "指纹配置超过镜像支持的大小"],
  ["operation_rejected", "原指纹还有未停止或未清理的任务"],
])("preserves the reviewed %s failure for the operator", (code, message) => {
  const error = new ApiError(message, 502, { detail: { code, message } });
  expect(safeAutomationOperationMessage(error)).toBe(message);
});
