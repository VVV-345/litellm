import { describe, expect, it } from "vitest";

import {
  canAuthorizeEnvironment,
  canConfigureEnvironment,
  canDeleteEnvironment,
  canToggleEnvironment,
  concurrencyLimitLabel,
  validateAccountPoolUpdate,
  validateProxyProfileSelection,
} from "./AccountPoolFormatters";
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

describe("account pool lifecycle controls", () => {
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
    expect(validateProxyProfileSelection("profile", null, profiles)).toBe("请选择代理 Profile");
    expect(validateProxyProfileSelection("profile", "missing", profiles)).toContain("已删除");
    expect(
      validateProxyProfileSelection("profile", "office", [
        { id: "office", name: "办公代理", proxy_url: "ftp://proxy.example.test" },
      ]),
    ).toContain("协议不安全");
  });

  it("requires the current environment version in a valid update", () => {
    const current = environment("ready");
    const request = toUpdateRequest(current);

    expect(request.version).toBe(4);
    expect(validateAccountPoolUpdate(request, current, profiles)).toBeNull();
    expect(
      validateAccountPoolUpdate(
        { ...request, version: 3, proxy_mode: "profile", proxy_profile_id: null },
        current,
        profiles,
      ),
    ).toBe("请选择代理 Profile");
  });

  it("labels concurrency as an environment-wide limit", () => {
    expect(concurrencyLimitLabel()).toBe("环境总并发");
  });
});
