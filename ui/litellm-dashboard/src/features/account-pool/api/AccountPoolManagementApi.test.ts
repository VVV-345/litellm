import { beforeEach, describe, expect, it, vi } from "vitest";
import { setServerRootPath } from "@/lib/serverRootPath";

import {
  analyzeAccountPoolUpstream,
  cancelAccountPoolOAuthBrowserSession,
  createAccountPoolJobId,
  getAccountPoolCodexReview,
  getAccountPoolUpstreamSync,
  getAccountPoolOAuthBrowserSession,
  getAccountPoolOAuthBrowserUrl,
  installCardAccountPoolPlugin,
  getAccountPoolAuthFileRefreshStatus,
  promoteAccountPoolUpstream,
  refreshAccountPoolAuthFiles,
  setAccountPoolAuthFileRefreshInterval,
  submitAccountPoolBatch,
  startAccountPoolOAuthBrowserSession,
  uploadAccountPoolAuthFile,
  type AccountPolicy,
} from "./AccountPoolManagementApi";

const getMock = vi.fn();
const postMock = vi.fn();
const putMock = vi.fn();
const deleteMock = vi.fn();

it.each([false, true])("passes explicit credential replacement intent: %s", async (replace) => {
  const file = new File(["{}"], "auth.json", { type: "application/json" });
  await uploadAccountPoolAuthFile("token", "card", file, replace);
  const options = postMock.mock.lastCall?.[1] as { rawBody: FormData };
  expect(options.rawBody.get("card_id")).toBe("card");
  expect(options.rawBody.get("replace")).toBe(String(replace));
  expect(options.rawBody.get("file")).toBeInstanceOf(File);
});

vi.mock("@/components/networking", () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    put: (...args: unknown[]) => putMock(...args),
    delete: (...args: unknown[]) => deleteMock(...args),
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
    getMock.mockReset();
    getMock.mockResolvedValue({});
    postMock.mockReset();
    postMock.mockResolvedValue({});
    putMock.mockReset();
    putMock.mockResolvedValue({});
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

  it("uses the authentication refresh routes and interval payload", async () => {
    await refreshAccountPoolAuthFiles("token-123");
    await getAccountPoolAuthFileRefreshStatus("token-123");
    await setAccountPoolAuthFileRefreshInterval("token-123", 15);

    expect(postMock).toHaveBeenCalledWith("/account_pool/auth-files/refresh", { accessToken: "token-123" });
    expect(getMock).toHaveBeenCalledWith("/account_pool/auth-files/refresh/status", { accessToken: "token-123" });
    expect(putMock).toHaveBeenCalledWith("/account_pool/auth-files/refresh/interval", {
      accessToken: "token-123",
      body: { interval_minutes: 15 },
    });
  });

  it("uses the dedicated upstream status, review, analysis, and promotion routes", async () => {
    await getAccountPoolUpstreamSync("token-123");
    await getAccountPoolCodexReview("token-123");
    await analyzeAccountPoolUpstream("token-123");
    await promoteAccountPoolUpstream("token-123");

    expect(getMock).toHaveBeenNthCalledWith(1, "/account_pool/upstream-sync", { accessToken: "token-123" });
    expect(getMock).toHaveBeenNthCalledWith(2, "/account_pool/upstream-sync/codex-review", {
      accessToken: "token-123",
    });
    expect(postMock).toHaveBeenNthCalledWith(1, "/account_pool/upstream-sync/analyze", {
      accessToken: "token-123",
    });
    expect(postMock).toHaveBeenNthCalledWith(2, "/account_pool/upstream-sync/promote", {
      accessToken: "token-123",
    });
  });
});

describe("OAuth browser session", () => {
  const session = {
    id: "session-1",
    environment_id: "card-1",
    status: "active",
    created_at: "2026-10-08T00:00:00Z",
    expires_at: "2026-10-08T00:05:00Z",
  };

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("exchanges the single-use ticket in a header before returning ticket-free session data", async () => {
    postMock.mockResolvedValueOnce({ ...session, ticket: "single-use-ticket" }).mockResolvedValueOnce(undefined);

    expect(await startAccountPoolOAuthBrowserSession("admin-token", "card/1")).toEqual(session);
    expect(postMock).toHaveBeenNthCalledWith(1, "/account_pool/environments/card%2F1/oauth-browser-sessions", {
      accessToken: "admin-token",
    });
    expect(postMock).toHaveBeenNthCalledWith(2, "/account_pool/oauth-browser-sessions/session-1/browser", {
      accessToken: "single-use-ticket",
    });
  });

  it("cancels a newly started worker if the ticket exchange fails", async () => {
    postMock
      .mockResolvedValueOnce({ ...session, ticket: "single-use-ticket" })
      .mockRejectedValueOnce(new Error("expired"));
    deleteMock.mockResolvedValue({ ...session, status: "cancelled" });

    await expect(startAccountPoolOAuthBrowserSession("admin-token", "card-1")).rejects.toThrow("expired");
    expect(deleteMock).toHaveBeenCalledWith("/account_pool/oauth-browser-sessions/session-1", {
      accessToken: "admin-token",
    });
  });

  it("uses a clean noVNC page and a session-scoped WebSocket path", () => {
    expect(getAccountPoolOAuthBrowserUrl("session-1")).toBe(
      "/account_pool/oauth-browser-sessions/session-1/browser/vnc.html?autoconnect=true&resize=scale&path=account_pool%2Foauth-browser-sessions%2Fsession-1%2Fbrowser%2Fwebsockify",
    );
  });

  it.each(["/team/litellm/", " team/litellm "])("preserves the normalized deployment prefix for %s", (rootPath) => {
    try {
      setServerRootPath(rootPath);
      expect(getAccountPoolOAuthBrowserUrl("session-1")).toBe(
        "/team/litellm/account_pool/oauth-browser-sessions/session-1/browser/vnc.html?autoconnect=true&resize=scale&path=team%2Flitellm%2Faccount_pool%2Foauth-browser-sessions%2Fsession-1%2Fbrowser%2Fwebsockify",
      );
    } finally {
      setServerRootPath("/");
    }
  });

  it("encodes lifecycle identities and bypasses cached status responses", async () => {
    const signal = new AbortController().signal;
    await getAccountPoolOAuthBrowserSession("admin-token", "session/1", signal);
    await cancelAccountPoolOAuthBrowserSession("admin-token", "session/1");

    expect(getMock).toHaveBeenCalledWith("/account_pool/oauth-browser-sessions/session%2F1", {
      accessToken: "admin-token",
      signal,
      cache: "no-store",
    });
    expect(deleteMock).toHaveBeenCalledWith("/account_pool/oauth-browser-sessions/session%2F1", {
      accessToken: "admin-token",
    });
  });
});
