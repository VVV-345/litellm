import { beforeEach, describe, expect, it, vi } from "vitest";

import { submitAccountPoolBatch, type AccountPolicy } from "./AccountPoolManagementApi";

const postMock = vi.fn();

vi.mock("@/components/networking", () => ({
  apiClient: {
    post: (...args: unknown[]) => postMock(...args),
  },
}));

const policy: AccountPolicy = {
  tags: ["premium"],
  group: "codex",
  account_ids: [],
  routing: {
    strategy: "priority",
    priority: 10,
    weight: 2,
    is_backup: false,
    preferred_account_ids: [],
    session_affinity: false,
    session_affinity_ttl: 3600,
    quota_reserve_percent: 0,
    quota_snapshot_max_age: 300,
    max_attempts: 1,
    retryable_statuses: [429, 502, 503, 504],
    backoff_ms: 1000,
    fallback_enabled: false,
  },
  excluded_models: [],
  model_aliases: [],
  transport: {
    image_generation: "inherit",
    websocket: "inherit",
    request_timeout_seconds: 120,
    debug_log_enabled: false,
  },
};

describe("submitAccountPoolBatch", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({});
  });

  it("sends the complete policy and target version snapshots", async () => {
    const targets = [
      {
        account_id: "00000000-0000-4000-8000-000000000001",
        version: 7,
        policy_version: 3,
      },
    ];

    await submitAccountPoolBatch("token-123", "policy", targets, policy);

    expect(postMock).toHaveBeenCalledWith("/account_pool/batches", {
      accessToken: "token-123",
      body: {
        job_id: expect.any(String),
        action: "policy",
        targets,
        policy,
      },
    });
  });
});
