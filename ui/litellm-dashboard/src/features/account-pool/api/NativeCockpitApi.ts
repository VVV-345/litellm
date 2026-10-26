import { z } from "zod";
import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

const root = "/account_pool/cockpit/native";
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const revision = z.string().regex(/^"sha256-[0-9a-f]{64}"$/);
export type NativeScopeStatus = components["schemas"]["NativeScopeStatus"];
export type NativePreflight = Pick<
  components["schemas"]["NativePreflightResponse"],
  "key_hash" | "status" | "revision" | "models"
>;
export type NativeReceipt = Pick<components["schemas"]["NativeReceiptResponse"], "key_hash" | "status" | "receipt">;

const scopeFields = {
  subject_hash: digest,
  state: z.enum(["absent", "staged", "active", "blocked"]),
  account_ids: z.array(z.string()),
  model_scope: z.enum(["none", "restricted"]),
  models: z.array(z.string()),
  persisted_manifest_revision: revision.nullable(),
  runtime_activation: z.literal("restart_required_or_unverified"),
  ready_to_migrate: z.literal(false),
};
const runtimeFields = {
  persisted_revision: revision,
  active_revision: revision.nullable(),
  apply_supported: z.boolean(),
  blocked_reason: z.enum(["non_policy_change", "unsupported_policy", "runtime_unavailable"]).nullable(),
};
const scopeStatus: z.ZodType<NativeScopeStatus> = z
  .object({
    scope: z.object(scopeFields),
    runtime: z.object(runtimeFields).nullable(),
    runtime_state: z.enum(["active", "pending", "unavailable"]),
  })
  .refine((value) => {
    if (value.runtime_state !== "active") return true;
    if (value.scope.state !== "active" || value.runtime === null) return false;
    const confirmed =
      value.runtime.active_revision === value.runtime.persisted_revision &&
      value.runtime.active_revision === value.scope.persisted_manifest_revision;
    return confirmed;
  });
const preflightFields = {
  key_hash: digest,
  status: z.enum(["eligible", "blocked"]),
  revision: revision.nullable(),
  models: z.array(z.string()),
};

const receiptShape: z.ZodType<NativeReceipt> = z
  .object({
    key_hash: digest,
    status: z.enum(["native_scope_verified", "rejected"]),
    receipt: z.string().max(16384).nullable(),
  })
  .refine((value) => (value.status === "native_scope_verified") === (value.receipt !== null));

function sameSubject<T>(value: T, actual: string, expected: string): T {
  if (actual !== expected) throw new Error("返回主体不一致，请重新读取");
  return value;
}

export const getNativeBackend = async (accessToken: string) =>
  z
    .object({ backend: z.enum(["manager", "cockpit"]) })
    .parse(await apiClient.get<unknown>(root + "/configuration", { accessToken, cache: "no-store" }));

export const getNativeCockpitAccounts = async (accessToken: string) =>
  z
    .array(
      z.object({
        account_id: z.string().uuid(),
        credential_present: z.boolean(),
      }),
    )
    .parse(await apiClient.get<unknown>(root + "/accounts", { accessToken, cache: "no-store" }));

export async function preflightNativeCockpit(accessToken: string, key_hash: string): Promise<NativePreflight> {
  const value = z
    .object(preflightFields)
    .parse(await apiClient.post<unknown>(root + "/preflight", { accessToken, body: { key_hash } }));
  return sameSubject(value, value.key_hash, key_hash);
}

export async function nativeReceipt(accessToken: string, key_hash: string, receipt?: string): Promise<NativeReceipt> {
  const value = receiptShape.parse(
    await apiClient.post<unknown>(root + "/credential-receipts" + (receipt ? "/revalidate" : ""), {
      accessToken,
      body: receipt ? { key_hash, receipt } : { key_hash },
    }),
  );
  return sameSubject(value, value.key_hash, key_hash);
}

export async function readNativeScope(accessToken: string, subject: string): Promise<NativeScopeStatus> {
  const value = scopeStatus.parse(
    await apiClient.get<unknown>(root + "/scope/" + digest.parse(subject), { accessToken, cache: "no-store" }),
  );
  return sameSubject(value, value.scope.subject_hash, subject);
}

export async function changeNativeScope(
  accessToken: string,
  action: "publish" | "activate" | "deactivate" | "revoke",
  body: components["schemas"]["CockpitScopeRevokeRequest"] & { receipt?: string },
): Promise<NativeScopeStatus> {
  const path = root + "/scope" + (action === "activate" || action === "deactivate" ? "/" + action : "");
  const response =
    action === "revoke"
      ? await apiClient.delete<unknown>(path, { accessToken, body })
      : await apiClient.post<unknown>(path, { accessToken, body });
  const value = scopeStatus.parse(response);
  return sameSubject(value, value.scope.subject_hash, body.subject_hash);
}
