/** 本文件验证预览和保存共用的设置 JSON 解析边界。 */

import { describe, expect, it } from "vitest";

import type { AccountPoolSettings } from "./AccountPoolManagementApi";
import { parseAccountPoolSettingsEditors } from "./AccountPoolSettingsEditors";

const settings = {
  debug_logging_enabled: false,
  default_concurrency_limit: 1,
  default_model_discovery: true,
  default_proxy_profile_id: null,
  default_route: "auto",
  error_logs_max_files: 10,
  file_logging_enabled: false,
  force_model_prefix: false,
  logs_max_total_size_mb: 0,
  max_attempts: 1,
  max_retry_credentials: 1,
  max_retry_interval: 0,
  oauth_excluded_models: [],
  oauth_model_aliases: {},
  payload: { default: [], "default-raw": [], override: [], "override-raw": [], filter: [] },
  oauth_request_scoped_errors: {},
  plugins_enabled: false,
  quota_switch_preview_model: false,
  quota_switch_project: false,
  request_log_enabled: false,
  request_retry: 1,
  request_timeout_seconds: 120,
  streaming_rules: [],
  usage_statistics_enabled: false,
  websocket_auth_enabled: false,
  websocket_enabled: false,
} satisfies AccountPoolSettings;

describe("parseAccountPoolSettingsEditors", () => {
  it("uses the current payload and OAuth editor content for a request", () => {
    const result = parseAccountPoolSettingsEditors(
      settings,
      '{"default":[{"models":[{"name":"gpt-*"}]}]}',
      '{"codex":[{"status":400,"action":"stop"}]}',
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.values.payload.default).toHaveLength(1);
    expect(result.values.oauth_request_scoped_errors.codex).toHaveLength(1);
  });

  it("reports invalid editors independently", () => {
    expect(parseAccountPoolSettingsEditors(settings, "[]", "{}")).toEqual({
      ok: false,
      payloadInvalid: true,
      oauthErrorsInvalid: false,
    });
    expect(parseAccountPoolSettingsEditors(settings, "{}", "invalid")).toEqual({
      ok: false,
      payloadInvalid: false,
      oauthErrorsInvalid: true,
    });
  });
});
