import { describe, expect, it } from "vitest";
import i18next from "@/i18n";

import {
  canAuthorizeEnvironment,
  canConfigureEnvironment,
  canDeleteEnvironment,
  canManageAccountPool,
  canToggleEnvironment,
} from "./AccountPoolPermissions";
import { concurrencyLimitLabel, formatQuota, mostConstrainedWindow, statusLabel } from "./AccountPoolFormatters";
import { validateAccountPoolUpdate, validateProxyProfileSelection } from "./AccountPoolValidation";
import { toUpdateRequest } from "./AccountPoolTypes";
import type { AccountPoolEnvironment, AccountPoolProxyProfile } from "./AccountPoolTypes";

const environment = (status: AccountPoolEnvironment["status"]): AccountPoolEnvironment => ({
  id: "00000000-0000-4000-8000-000000000001",
  version: 4,
  configuration_pending: false,
  name: "测试环境",
  provider: "openai",
  status,
  enabled: true,
  manual_cooldown: false,
  concurrency_limit: 2,
  proxy_mode: "default_gateway",
  proxy_profile_id: null,
  available_models: ["gpt-4o"],
  enabled_models: ["gpt-4o"],
  quota: { observed_at: null, plan_type: null, windows: [] },
  model_quotas: [],
  cooldown_until: null,
  last_error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const profiles: readonly AccountPoolProxyProfile[] = [
  { id: "office", name: "办公代理", proxy_url: "https://proxy.example.test:8443" },
];
const english = i18next.getFixedT("en");
const chinese = i18next.getFixedT("zh-CN");

describe("account pool lifecycle controls", () => {
  it("blocks account pool management for view-only roles", () => {
    expect(canManageAccountPool("proxy_admin", false)).toBe(true);
    expect(canManageAccountPool("proxy_admin_viewer", true)).toBe(false);
    expect(canManageAccountPool("Admin Viewer", true)).toBe(false);
  });

  it("blocks mutations while an environment is transitioning", () => {
    const pending = environment("validating");
    const deleting = environment("deleting");

    expect(canToggleEnvironment(pending)).toBe(false);
    expect(canConfigureEnvironment(pending)).toBe(false);
    expect(canDeleteEnvironment(pending)).toBe(true);
    expect(canDeleteEnvironment(deleting)).toBe(false);
    expect(canAuthorizeEnvironment(environment("awaiting_authorization"))).toBe(true);
  });

  it("rejects missing, removed, and unsafe proxy profiles before saving", () => {
    expect(validateProxyProfileSelection(chinese, "profile", null, profiles)).toBe("请选择代理 Profile");
    expect(validateProxyProfileSelection(chinese, "profile", "missing", profiles)).toContain("已删除");
    expect(
      validateProxyProfileSelection(chinese, "profile", "office", [
        { id: "office", name: "办公代理", proxy_url: "ftp://proxy.example.test" },
      ]),
    ).toContain("仅支持 HTTP 或 HTTPS");
  });

  it("requires the current environment version in a valid update", () => {
    const current = environment("ready");
    const request = toUpdateRequest(current);

    expect(request.version).toBe(4);
    expect(validateAccountPoolUpdate(chinese, request, current, profiles)).toBeNull();
    expect(
      validateAccountPoolUpdate(
        chinese,
        { ...request, version: 3, proxy_mode: "profile", proxy_profile_id: null },
        current,
        profiles,
      ),
    ).toBe("请选择代理 Profile");
  });

  it("labels concurrency as an environment-wide limit", () => {
    expect(concurrencyLimitLabel(chinese)).toBe("环境总并发");
  });

  it("labels the most constrained quota window", () => {
    const current = environment("cooling_down");
    const withQuota = {
      ...current,
      quota: {
        observed_at: "2026-01-01T00:00:00Z",
        plan_type: "pro",
        windows: [
          {
            name: "Weekly",
            used_percent: 80,
            remaining_percent: 20,
            window_minutes: 10080,
            resets_at: "2026-01-08T00:00:00Z",
          },
          {
            name: "Monthly",
            used_percent: 95,
            remaining_percent: 5,
            window_minutes: 43200,
            resets_at: "2026-02-01T00:00:00Z",
          },
        ],
      },
    };

    expect(mostConstrainedWindow(withQuota)?.name).toBe("Monthly");
    expect(formatQuota(chinese, mostConstrainedWindow(withQuota))).toBe("5%");
  });

  it("loads account pool labels from the active locale", () => {
    expect(statusLabel(english, "ready")).toBe("Ready");
    expect(statusLabel(chinese, "ready")).toBe("可用");
    expect(formatQuota(english, null)).toBe("Not observed yet");
    expect(formatQuota(chinese, null)).toBe("尚未观测");
  });
});
