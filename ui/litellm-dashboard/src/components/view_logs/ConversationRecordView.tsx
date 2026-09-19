import { useState } from "react";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "@/lib/toast";
import type { FullLogFilters, FullLogRecord } from "./fullLogsApi";
import {
  conversationInput,
  conversationOutput,
  currentConversationInput,
  type ConversationMessage,
} from "./logConversation";
import {
  blockLabel,
  conversationSample,
  downloadConversation,
  fetchConversationRecords,
  roleLabel,
} from "./conversationExport";

function MessageCard({ message }: { message: ConversationMessage }) {
  return (
    <article
      className={`min-w-0 rounded-xl border p-4 ${message.role === "assistant" ? "border-primary/20 bg-primary/5" : "bg-muted/20"}`}
    >
      <h4 className="mb-3 text-sm font-semibold">
        {roleLabel(
          message.blocks.length > 0 && message.blocks.every((block) => block.kind === "tool_result")
            ? "tool"
            : message.role,
        )}
      </h4>
      <div className="grid gap-3">
        {message.blocks.map((block, index) => {
          const content = (
            <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-7">
              {block.text}
            </pre>
          );
          if (block.kind === "text") return <div key={index}>{content}</div>;
          const label = blockLabel(block);
          return (
            <details
              key={index}
              className="min-w-0 rounded-lg border bg-background/60 p-3"
              open={block.kind === "tool_call" || block.kind === "tool_result"}
            >
              <summary className="cursor-pointer text-sm font-medium">{label}</summary>
              {block.id && (
                <p className="my-2 break-all font-mono text-xs text-muted-foreground">调用 ID：{block.id}</p>
              )}
              <div className="mt-2">{content}</div>
            </details>
          );
        })}
      </div>
    </article>
  );
}

export function ConversationRecordView({ log }: { log: FullLogRecord }) {
  const [fullContext, setFullContext] = useState(false);
  const inputs = conversationInput(log.request);
  const outputs = conversationOutput(log.response);
  const sample = conversationSample(log);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(sample, null, 2));
      toast.success("已复制结构化对话");
    } catch {
      toast.error("复制失败，请使用导出");
    }
  };
  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border p-4">
        <div>
          <p className="font-medium">{log.model}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {new Date(log.started_at).toLocaleString()} · 第 {log.attempt} 次尝试 · HTTP {log.result.http_status}
          </p>
        </div>
        <Badge variant={sample.complete ? "secondary" : "destructive"}>
          {sample.complete ? "完整记录" : "需要检查"}
        </Badge>
      </div>
      {sample.warnings.length > 0 && (
        <div role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {sample.warnings.join("；")}。导出会保留这些标记
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant={fullContext ? "outline" : "default"} size="sm" onClick={() => setFullContext(false)}>
          本轮内容
        </Button>
        <Button variant={fullContext ? "default" : "outline"} size="sm" onClick={() => setFullContext(true)}>
          完整上下文（{inputs.length} 条）
        </Button>
        <Button variant="outline" size="sm" onClick={() => void copy()}>
          <Copy className="size-4" />
          复制对话
        </Button>
        <Button variant="outline" size="sm" onClick={() => downloadConversation([log], "markdown")}>
          <Download className="size-4" />
          导出 Markdown
        </Button>
        <Button variant="outline" size="sm" onClick={() => downloadConversation([log], "jsonl")}>
          导出 JSONL
        </Button>
        <Button variant="ghost" size="sm" onClick={() => downloadConversation([log], "raw")}>
          导出原始记录
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        JSONL 每行对应一次请求，包含完整可读上下文、模型输出及工具调用；重试与失败单独标记。加密推理不能还原成思考文本
      </p>
      {(fullContext ? inputs : currentConversationInput(inputs)).map((message, index) => (
        <MessageCard key={`in-${index}`} message={message} />
      ))}
      <div className="flex items-center gap-3 text-xs font-medium text-muted-foreground">
        <div className="h-px flex-1 bg-border" />
        本次模型输出
        <div className="h-px flex-1 bg-border" />
      </div>
      {outputs.length ? (
        outputs.map((message, index) => <MessageCard key={`out-${index}`} message={message} />)
      ) : (
        <p className="rounded-lg border p-4 text-sm">没有收到可读模型输出，可展开原始记录检查错误</p>
      )}
      {!fullContext && inputs.some((message) => ["system", "developer"].includes(message.role)) && (
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer text-sm">提示词</summary>
          <div className="mt-3 grid gap-3">
            {inputs
              .filter((message) => ["system", "developer"].includes(message.role))
              .map((message, index) => (
                <MessageCard key={index} message={message} />
              ))}
          </div>
        </details>
      )}
      <dl className="grid grid-cols-2 gap-4 rounded-xl border p-4 text-sm sm:grid-cols-4">
        {[
          ["输入 Token", log.result.input_tokens],
          ["输出 Token", log.result.output_tokens],
          ["缓存读取", log.result.cache_read_input_tokens],
          ["费用（USD）", log.result.cost_usd == null ? "计价未知" : `$${log.result.cost_usd.toFixed(8)}`],
        ].map(([name, value]) => (
          <div key={name}>
            <dt className="text-xs text-muted-foreground">{name}</dt>
            <dd className="mt-1 font-mono">{value ?? "未知"}</dd>
          </div>
        ))}
      </dl>
      <p className="break-all text-xs text-muted-foreground">
        会话：{log.session_id ?? "未关联"} · 请求：{log.request_id}
      </p>
      <details className="min-w-0 rounded-lg border p-3">
        <summary className="cursor-pointer text-sm">原始完整记录（包含协议、计价依据和错误）</summary>
        <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">
          {JSON.stringify(log, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export function ConversationExport({ accessToken, filters }: { accessToken: string; filters: FullLogFilters }) {
  const [progress, setProgress] = useState<number | null>(null);
  const run = async (format: "jsonl" | "markdown" | "raw") => {
    setProgress(0);
    try {
      const records = await fetchConversationRecords(accessToken, filters, setProgress);
      downloadConversation(records, format);
      toast.success(`已导出 ${records.length} 条记录`);
    } catch (error) {
      toast.fromError(error);
    } finally {
      setProgress(null);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" disabled={progress !== null} onClick={() => void run("markdown")}>
        导出会话 Markdown
      </Button>
      <Button variant="outline" size="sm" disabled={progress !== null} onClick={() => void run("jsonl")}>
        导出会话 JSONL
      </Button>
      <Button variant="ghost" size="sm" disabled={progress !== null} onClick={() => void run("raw")}>
        导出会话原始记录
      </Button>
      {progress !== null && (
        <span role="status" className="text-xs text-muted-foreground">
          已读取 {progress} 条
        </span>
      )}
    </div>
  );
}
