import type { FeedbackCreate, FeedbackIssue, FeedbackMutation } from "../../api/AccountPoolFeedbackApi";

export const FEEDBACK_STATUSES = ["pending", "in_progress", "pending_verification", "resolved"] as const;
export const FEEDBACK_FIELDS = [
  { name: "title", maxLength: 200, required: true },
  { name: "description", maxLength: 8000, required: true, multiline: true },
  { name: "steps", maxLength: 8000, multiline: true },
  { name: "module", maxLength: 80 },
  { name: "page_path", maxLength: 512 },
  { name: "discovered_version", maxLength: 160 },
] as const;

export const feedbackFormPayload = (data: FormData): FeedbackCreate => ({
  title: String(data.get("title") ?? "").trim(),
  description: String(data.get("description") ?? "").trim(),
  steps: String(data.get("steps") ?? ""),
  module: String(data.get("module") ?? ""),
  page_path: String(data.get("page_path") ?? ""),
  discovered_version: String(data.get("discovered_version") ?? ""),
  priority: data.get("priority") as FeedbackCreate["priority"],
});

export const feedbackActions = (status: FeedbackIssue["status"]): FeedbackMutation["action"][] => {
  if (status === "pending") return ["claim"];
  if (status === "in_progress") return ["submit_fix", "note", "release", "reopen"];
  if (status === "pending_verification") return ["resolve", "reopen"];
  return ["reopen"];
};
