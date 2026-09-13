import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAccountPoolJobId,
  installCardAccountPoolPlugin,
  submitAccountPoolBatch,
  type AccountPolicy,
} from "./AccountPoolManagementApi";

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
    token_budget_window_seconds: 3600,
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

  it("creates a valid job id when randomUUID is unavailable on an HTTP origin", () => {
    const originalCrypto = globalThis.crypto;
    try {
      vi.stubGlobal("crypto", { getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto) });
      expect(createAccountPoolJobId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    } finally {
      vi.stubGlobal("crypto", originalCrypto);
    }
  });

  it("sends the selected plugin version and store source", async () => {
    await installCardAccountPoolPlugin("token-123", "card-1", "plugin/with spaces", {
      version: "1.2.3",
      source: "official",
    });

    expect(postMock).toHaveBeenCalledWith("/account_pool/environments/card-1/plugins/plugin%2Fwith%20spaces/install", {
      accessToken: "token-123",
      body: { version: "1.2.3", source: "official" },
    });
  });
});
