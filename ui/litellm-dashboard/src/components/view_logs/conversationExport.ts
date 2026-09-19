import { getFullLog, listFullLogs, type FullLogRecord, type FullLogFilters } from "./fullLogsApi";
import {
  conversationInput,
  conversationOutput,
  type ConversationMessage,
  type ConversationBlock,
} from "./logConversation";

export const roleLabel = (role: string) =>
  ({ system: "系统提示词", developer: "开发者指令", user: "用户", assistant: "模型", tool: "工具结果" })[role] ?? role;

export function blockLabel(block: ConversationBlock): string {
  if (block.kind === "tool_call") return `调用工具 · ${block.name ?? "工具"}`;
  return { text: "文本", thinking: "思考内容", tool_result: "工具返回结果", attachment: "附件", other: "其他内容" }[
    block.kind
  ];
}

export function conversationSample(log: FullLogRecord) {
  const output = conversationOutput(log.response);
  const messages = [...conversationInput(log.request), ...output];
  const warnings = [
    ...(log.incomplete || log.result.http_status >= 400 ? ["请求失败或回复未完成"] : []),
    ...(log.truncated ? ["保存内容已截断"] : []),
    ...(log.transport === "websocket" ? ["WebSocket 可能包含多轮交换；请结合原始事件核对请求与回复顺序"] : []),
    ...(output.length === 0 ? ["没有可导出的模型输出"] : []),
    ...(messages.some((message) => message.blocks.some((block) => block.kind === "attachment"))
      ? ["包含附件；请检查原始数据是否完整"]
      : []),
    ...(messages.some((message) => message.blocks.some((block) => block.kind === "other"))
      ? ["包含加密推理或未转换的内容块；请在蒸馏前筛选"]
      : []),
    ...(JSON.stringify(log.request).includes("[REDACTED]") ? ["请求中包含脱敏内容"] : []),
    ...(log.result.detail?.includes("signature_recovery") ? ["本次重试已清理不兼容的隐藏思考状态"] : []),
  ];
  return {
    schema_version: "conversation-v1",
    request_id: log.request_id,
    event_id: log.event_id,
    session_id: log.session_id,
    model: log.model,
    requested_model: log.requested_model,
    started_at: log.started_at,
    attempt: log.attempt,
    status: log.result.http_status,
    complete: !log.incomplete && !log.truncated && log.result.http_status < 400 && output.length > 0,
    review_required: warnings.length > 0,
    warnings,
    messages,
    tools: log.request && typeof log.request === "object" && "tools" in log.request ? log.request.tools : undefined,
    usage: {
      input_tokens: log.result.input_tokens,
      output_tokens: log.result.output_tokens,
      cache_read_input_tokens: log.result.cache_read_input_tokens,
      cache_creation_input_tokens: log.result.cache_creation_input_tokens,
    },
    cost_usd: log.result.cost_usd,
  };
}

export async function fetchConversationRecords(
  accessToken: string,
  filters: FullLogFilters,
  progress: (count: number) => void,
): Promise<FullLogRecord[]> {
  const snapshot = { ...filters, occurred_to: filters.occurred_to ?? new Date().toISOString(), limit: 100 };
  const records: FullLogRecord[] = [];
  let offset = 0;
  let bytes = 0;
  for (;;) {
    const page = await listFullLogs(accessToken, { ...snapshot, offset });
    for (const item of page.items) {
      const record = await getFullLog(accessToken, item.event_id);
      bytes += new TextEncoder().encode(JSON.stringify(record)).length;
      if (bytes > 64 * 1024 * 1024) throw new Error("导出内容超过 64 MiB，请缩小时间范围后重试");
      records.push(record);
      progress(records.length);
    }
    if (!page.has_more) break;
    if (!page.items.length || offset + 100 > 100000) throw new Error("会话分页异常，请缩小时间范围后重试");
    offset += 100;
  }
  if (!records.length) throw new Error("当前会话没有可导出的记录");
  return records;
}

function markdownMessage(message: ConversationMessage): string {
  return (
    `### ${roleLabel(message.role)}\n\n` +
    message.blocks
      .map((block) => {
        if (block.kind === "text") return block.text;
        const label = blockLabel(block);
        return `**${label}${block.id ? ` · ${block.id}` : ""}**\n\n${block.text}`;
      })
      .join("\n\n")
  );
}

export function exportConversation(records: FullLogRecord[], format: "jsonl" | "markdown" | "raw"): string {
  if (format === "raw") return JSON.stringify({ schema_version: "full-log-v1", records }, null, 2);
  if (format === "jsonl") return records.map((record) => JSON.stringify(conversationSample(record))).join("\n") + "\n";
  return records
    .map((record, index) => {
      const sample = conversationSample(record);
      return `# 记录 ${index + 1} · ${record.model}\n\n请求：${record.request_id}\n\n时间：${record.started_at} · 尝试：${record.attempt} · HTTP ${sample.status}\n\n${sample.warnings.map((warning) => `> ${warning}`).join("\n")}\n\n${sample.messages.map(markdownMessage).join("\n\n")}\n`;
    })
    .join("\n---\n\n");
}

export function downloadConversation(records: FullLogRecord[], format: "jsonl" | "markdown" | "raw") {
  const extension = { markdown: "md", raw: "json", jsonl: "jsonl" }[format];
  const blob = new Blob([exportConversation(records, format)], {
    type: format === "markdown" ? "text/markdown;charset=utf-8" : "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `conversation-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
