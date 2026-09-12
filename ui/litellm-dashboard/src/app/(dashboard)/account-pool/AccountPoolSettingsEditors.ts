/** 本文件统一解析全局设置中的 JSON 编辑器内容，并分别报告字段错误。 */

import type { AccountPoolSettings } from "./AccountPoolManagementApi";

type ParsedAccountPoolSettings = AccountPoolSettings & {
  payload: NonNullable<AccountPoolSettings["payload"]>;
  oauth_request_scoped_errors: NonNullable<AccountPoolSettings["oauth_request_scoped_errors"]>;
};

type SettingsEditorResult =
  | { ok: true; values: ParsedAccountPoolSettings }
  | { ok: false; payloadInvalid: boolean; oauthErrorsInvalid: boolean };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const parseObject = (text: string, fallback: unknown): Record<string, unknown> | null => {
  try {
    const value: unknown = text.trim() ? JSON.parse(text) : fallback;
    return isRecord(value) ? value : null;
  } catch {
    return null;
  }
};

export const parseAccountPoolSettingsEditors = (
  values: AccountPoolSettings,
  payloadText: string | null,
  oauthErrorsText: string | null,
): SettingsEditorResult => {
  const payload = parseObject(payloadText ?? "", values.payload);
  const oauthErrors = parseObject(oauthErrorsText ?? "", values.oauth_request_scoped_errors);
  if (payload === null || oauthErrors === null) {
    return {
      ok: false,
      payloadInvalid: payload === null,
      oauthErrorsInvalid: oauthErrors === null,
    };
  }
  return {
    ok: true,
    values: {
      ...values,
      payload: payload as ParsedAccountPoolSettings["payload"],
      oauth_request_scoped_errors: oauthErrors as ParsedAccountPoolSettings["oauth_request_scoped_errors"],
    },
  };
};
