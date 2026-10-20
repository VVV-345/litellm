import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createFeedbackToken,
  deleteFeedbackIssue,
  getFeedbackIssue,
  listFeedbackIssues,
  listFeedbackTokens,
  mutateFeedbackIssue,
  uploadFeedbackAttachment,
  downloadFeedbackAttachment,
} from "./AccountPoolFeedbackApi";

const getMock = vi.fn();
const postMock = vi.fn();
const deleteMock = vi.fn();
const requestBlobMock = vi.fn();
vi.mock("@/components/networking", () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    delete: (...args: unknown[]) => deleteMock(...args),
    requestBlob: (...args: unknown[]) => requestBlobMock(...args),
  },
}));

beforeEach(() => {
  vi.resetAllMocks();
});

describe("feedback API", () => {
  it("sends server-side filters and revision-constrained deletion", async () => {
    const query = { status: "unresolved" as const, q: "刷新", limit: 50, offset: 50 };
    await listFeedbackIssues("admin", query);
    await getFeedbackIssue("admin", "issue/1");
    await deleteFeedbackIssue("admin", "issue/1", 3);
    expect(getMock).toHaveBeenNthCalledWith(1, "/account_pool/feedback/issues", { accessToken: "admin", query });
    expect(getMock).toHaveBeenNthCalledWith(2, "/account_pool/feedback/issues/issue%2F1", { accessToken: "admin" });
    expect(deleteMock).toHaveBeenCalledWith("/account_pool/feedback/issues/issue%2F1", {
      accessToken: "admin", query: { revision: 3 },
    });
  });

  it("sends state changes with the expected revision", async () => {
    const body = { action: "claim" as const, expected_revision: 3, summary: "", commit: "", verification: "", deployed_version: "" };
    await mutateFeedbackIssue("admin", "issue-1", body);
    expect(postMock).toHaveBeenCalledWith("/account_pool/feedback/issues/issue-1/actions", { accessToken: "admin", body });
  });

  it("does not cache token metadata or one-time token issuance", async () => {
    await listFeedbackTokens("admin");
    await createFeedbackToken("admin", { name: "Codex", expires_in_days: 30 });
    expect(getMock).toHaveBeenCalledWith("/account_pool/feedback/tokens", { accessToken: "admin", cache: "no-store" });
    expect(postMock).toHaveBeenCalledWith("/account_pool/feedback/tokens", {
      accessToken: "admin", body: { name: "Codex", expires_in_days: 30 }, cache: "no-store",
    });
  });

  it("uploads a file as multipart and downloads it as a blob", async () => {
    const file = new File(["png"], "capture.png", { type: "image/png" });
    await uploadFeedbackAttachment("admin", "issue-1", 3, file);
    const [path, options] = postMock.mock.calls[0] as [string, { rawBody: FormData; accessToken: string }];
    expect(path).toBe("/account_pool/feedback/issues/issue-1/attachments");
    expect(options.accessToken).toBe("admin");
    expect(options.rawBody.get("expected_revision")).toBe("3");
    expect(options.rawBody.get("file")).toBe(file);
    await downloadFeedbackAttachment("admin", "issue-1", "attachment-1");
    expect(requestBlobMock).toHaveBeenCalledWith("GET", "/account_pool/feedback/issues/issue-1/attachments/attachment-1", { accessToken: "admin" });
  });
});
