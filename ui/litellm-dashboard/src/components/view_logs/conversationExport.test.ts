import { beforeEach, expect, it, vi } from "vitest";
import { conversationSample, exportConversation, fetchConversationRecords } from "./conversationExport";
import { getFullLog, listFullLogs, type FullLogRecord } from "./fullLogsApi";

vi.mock("./fullLogsApi", () => ({ getFullLog: vi.fn(), listFullLogs: vi.fn() }));
const record = {
  event_id: "event",
  request_id: "request",
  session_id: "session",
  model: "model",
  attempt: 1,
  started_at: "2026-09-19T01:00:00Z",
  incomplete: false,
  truncated: false,
  result: { http_status: 200, cost_usd: 0.00012, detail: "signature_recovery" },
  request: {
    messages: [
      { role: "system", content: "rules" },
      { role: "user", content: "old" },
      { role: "assistant", content: "prior answer" },
      { role: "user", content: "new" },
    ],
    tools: [{ name: "search" }],
  },
  response: { type: "message", role: "assistant", content: [{ type: "text", text: "answer" }] },
} as FullLogRecord;

beforeEach(() => vi.resetAllMocks());

it("exports full context and tool definitions with status and review markers", () => {
  const sample = conversationSample(record);
  expect(sample.messages.map((message) => message.role)).toEqual(["system", "user", "assistant", "user", "assistant"]);
  expect(sample.tools).toEqual([{ name: "search" }]);
  expect(sample.review_required).toBe(true);
  const lines = exportConversation([record, { ...record, incomplete: true }], "jsonl")
    .trim()
    .split("\n");
  expect(lines).toHaveLength(2);
  expect(JSON.parse(lines[1])).toMatchObject({ complete: false, review_required: true, cost_usd: 0.00012 });
  expect(exportConversation([record], "markdown")).toContain("### 系统提示词");
  expect(JSON.parse(exportConversation([record], "raw")).records).toEqual([record]);
});

it("loads every page in the same key and session scope before returning records", async () => {
  vi.mocked(listFullLogs)
    .mockResolvedValueOnce({ items: [record], has_more: true })
    .mockResolvedValueOnce({ items: [{ ...record, event_id: "event-2" }], has_more: false });
  vi.mocked(getFullLog)
    .mockResolvedValueOnce(record)
    .mockResolvedValueOnce({ ...record, event_id: "event-2" });
  const progress = vi.fn();
  const records = await fetchConversationRecords(
    "token",
    { session_id: "session", key_id: "key", offset: 50, limit: 25 },
    progress,
  );
  expect(records.map((item) => item.event_id)).toEqual(["event", "event-2"]);
  expect(listFullLogs).toHaveBeenNthCalledWith(
    1,
    "token",
    expect.objectContaining({ session_id: "session", key_id: "key", offset: 0, limit: 100 }),
  );
  expect(listFullLogs).toHaveBeenNthCalledWith(2, "token", expect.objectContaining({ offset: 100, limit: 100 }));
  expect(vi.mocked(listFullLogs).mock.calls[0][1].occurred_to).toBe(
    vi.mocked(listFullLogs).mock.calls[1][1].occurred_to,
  );
  expect(progress).toHaveBeenLastCalledWith(2);
});

it("rejects interrupted export without returning a partial dataset", async () => {
  vi.mocked(listFullLogs)
    .mockResolvedValueOnce({ items: [record], has_more: true })
    .mockRejectedValueOnce(new Error("network"));
  vi.mocked(getFullLog).mockResolvedValue(record);
  await expect(fetchConversationRecords("token", {}, vi.fn())).rejects.toThrow("network");
});
