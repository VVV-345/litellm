import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";

export type FeedbackIssue = components["schemas"]["FeedbackIssue"];
export type FeedbackAttachmentInfo = components["schemas"]["FeedbackAttachmentInfo"];
export type FeedbackPage = components["schemas"]["FeedbackPage"];
export type FeedbackCreate = components["schemas"]["FeedbackCreate"];
export type FeedbackEdit = components["schemas"]["FeedbackEdit"];
export type FeedbackMutation = components["schemas"]["FeedbackMutation"];
export type FeedbackTokenView = components["schemas"]["FeedbackTokenView"];
export type FeedbackTokenIssue = components["schemas"]["FeedbackTokenIssue"];
export type FeedbackTokenCreate = components["schemas"]["FeedbackTokenCreate"];
export type FeedbackFilter = "all" | "unresolved" | FeedbackIssue["status"];
export interface FeedbackQuery {
  status: FeedbackFilter;
  q: string;
  limit: number;
  offset: number;
}

const ROOT = "/account_pool/feedback";
const issuePath = (id: string) => `${ROOT}/issues/${encodeURIComponent(id)}`;

export const listFeedbackIssues = (accessToken: string, query: FeedbackQuery): Promise<FeedbackPage> =>
  apiClient.get(`${ROOT}/issues`, { accessToken, query: { ...query } });
export const getFeedbackIssue = (accessToken: string, id: string): Promise<FeedbackIssue> =>
  apiClient.get(issuePath(id), { accessToken });
export const createFeedbackIssue = (accessToken: string, body: FeedbackCreate): Promise<FeedbackIssue> =>
  apiClient.post(`${ROOT}/issues`, { accessToken, body });
export const editFeedbackIssue = (accessToken: string, id: string, body: FeedbackEdit): Promise<FeedbackIssue> =>
  apiClient.put(issuePath(id), { accessToken, body });
export const deleteFeedbackIssue = (accessToken: string, id: string, revision: number): Promise<void> =>
  apiClient.delete(issuePath(id), { accessToken, query: { revision } });
export const mutateFeedbackIssue = (accessToken: string, id: string, body: FeedbackMutation): Promise<FeedbackIssue> =>
  apiClient.post(`${issuePath(id)}/actions`, { accessToken, body });
export const uploadFeedbackAttachment = (
  accessToken: string, id: string, expectedRevision: number, file: File,
): Promise<FeedbackIssue> => {
  const body = new FormData();
  body.set("expected_revision", String(expectedRevision));
  body.set("file", file);
  return apiClient.post(`${issuePath(id)}/attachments`, { accessToken, rawBody: body });
};
export const downloadFeedbackAttachment = (accessToken: string, id: string, attachmentId: string): Promise<Blob> =>
  apiClient.requestBlob("GET", `${issuePath(id)}/attachments/${encodeURIComponent(attachmentId)}`, { accessToken });
export const listFeedbackTokens = (accessToken: string): Promise<FeedbackTokenView[]> =>
  apiClient.get(`${ROOT}/tokens`, { accessToken, cache: "no-store" });
export const createFeedbackToken = (accessToken: string, body: FeedbackTokenCreate): Promise<FeedbackTokenIssue> =>
  apiClient.post(`${ROOT}/tokens`, { accessToken, body, cache: "no-store" });
export const revokeFeedbackToken = (accessToken: string, id: string): Promise<void> =>
  apiClient.delete(`${ROOT}/tokens/${encodeURIComponent(id)}`, { accessToken });
