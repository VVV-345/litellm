/** 本文件从三种上游响应结构提取本轮可读消息，原始请求仍由完整日志保存。 */
type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as ObjectValue) : {};
const array = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
export const readableContent = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(readableContent).filter(Boolean).join("\n");
  const item = object(value);
  if (typeof item.text === "string") return item.text;
  if (item.content != null) return readableContent(item.content);
  return value == null ? "" : JSON.stringify(value, null, 2);
};

export function protocolEvents(value: unknown): ObjectValue[] {
  if (Array.isArray(value)) return value.flatMap(protocolEvents);
  if (typeof value !== "string") return [object(value)];
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.flatMap(protocolEvents) : [object(parsed)];
  } catch {
    /* SSE bodies are parsed frame by frame below. */
  }
  return value.split(/\r?\n/).flatMap((line) => {
    const raw = line.startsWith("data:") ? line.slice(5).trim() : line.trim();
    try {
      return [object(JSON.parse(raw))];
    } catch {
      return [];
    }
  });
}

export type ConversationBlock = {
  kind: "text" | "thinking" | "tool_call" | "tool_result" | "attachment" | "other";
  text: string;
  name?: string;
  id?: string;
  data?: unknown;
};
export type ConversationMessage = { role: string; blocks: ConversationBlock[] };

function contentBlocks(value: unknown): ConversationBlock[] {
  if (typeof value === "string") return value ? [{ kind: "text", text: value }] : [];
  if (Array.isArray(value)) return value.flatMap(contentBlocks);
  const block = object(value);
  const type = String(block.type ?? "");
  if (["text", "input_text", "output_text", "summary_text"].includes(type))
    return [{ kind: "text", text: String(block.text ?? "") }];
  if (type === "thinking") return [{ kind: "thinking", text: String(block.thinking ?? "") }];
  if (type === "redacted_thinking" || (type === "reasoning" && !readableContent(block.summary)))
    return [{ kind: "other", text: "上游仅返回加密推理，无法还原为思考文本", data: { type: "unavailable_reasoning" } }];
  if (type === "reasoning") return [{ kind: "thinking", text: readableContent(block.summary) }];
  if (["tool_use", "function_call", "custom_tool_call"].includes(type))
    return [
      {
        kind: "tool_call",
        text: readableContent(block.arguments ?? block.input),
        name: String(block.name ?? "工具"),
        id: String(block.call_id ?? block.id ?? ""),
        data: block.arguments ?? block.input,
      },
    ];
  if (["tool_result", "function_call_output", "custom_tool_call_output"].includes(type))
    return [
      {
        kind: "tool_result",
        text: readableContent(block.output ?? block.content),
        id: String(block.call_id ?? block.tool_use_id ?? ""),
        data: block.output ?? block.content,
      },
    ];
  if (["image", "image_url", "input_image", "input_audio", "audio", "document", "input_file", "file"].includes(type))
    return [
      {
        kind: "attachment",
        text: `${type} · ${String(block.filename ?? block.title ?? "附件；内联数据可能未保存")}`,
        data: block,
      },
    ];
  if (block.content != null) return contentBlocks(block.content);
  return value == null ? [] : [{ kind: "other", text: readableContent(value), data: value }];
}

function conversationMessage(value: unknown, fallback = "assistant"): ConversationMessage {
  const item = object(value);
  if (["function_call_output", "custom_tool_call_output"].includes(String(item.type)))
    return { role: "tool", blocks: contentBlocks(item) };
  const role = String(item.role ?? fallback);
  if (role === "tool" || role === "function")
    return {
      role: "tool",
      blocks: [
        {
          kind: "tool_result",
          text: readableContent(item.content),
          id: String(item.tool_call_id ?? item.name ?? ""),
          data: item.content,
        },
      ],
    };
  return {
    role,
    blocks: [
      ...contentBlocks(item.content ?? (item.type ? item : undefined)),
      ...contentBlocks(item.thinking_blocks),
      ...(item.reasoning_content ? [{ kind: "thinking" as const, text: String(item.reasoning_content) }] : []),
      ...array(item.tool_calls).map((call): ConversationBlock => {
        const tool = object(call);
        const fn = object(tool.function);
        return {
          kind: "tool_call",
          id: String(tool.id ?? ""),
          name: String(fn.name ?? "工具"),
          text: readableContent(fn.arguments),
          data: fn.arguments,
        };
      }),
    ],
  };
}

export function conversationInput(value: unknown): ConversationMessage[] {
  return protocolEvents(value).flatMap((event) => {
    const body = object(event.response ?? event);
    const instructions = body.instructions ?? body.system;
    const input = body.messages ?? body.input;
    return [
      ...(instructions ? [{ role: "system", blocks: contentBlocks(instructions) }] : []),
      ...(typeof input === "string"
        ? [{ role: "user", blocks: contentBlocks(input) }]
        : array(input).map((item) =>
            conversationMessage(
              item,
              ["reasoning", "function_call", "custom_tool_call"].includes(String(object(item).type))
                ? "assistant"
                : "user",
            ),
          )),
    ];
  });
}

export function conversationOutput(value: unknown): ConversationMessage[] {
  const events = protocolEvents(value);
  const completed = events.filter(
    (event) =>
      ["response.completed", "response.incomplete"].includes(String(event.type)) ||
      Array.isArray(object(event.response ?? event).output),
  );
  const finals = completed.filter((event, index) => {
    const id = object(event.response ?? event).id;
    return !id || completed.findLastIndex((item) => object(item.response ?? item).id === id) === index;
  });
  if (finals.length)
    return finals.flatMap((event) =>
      array(object(event.response ?? event).output).map((item) => conversationMessage(item)),
    );
  const chat = events
    .flatMap((event) => array(event.choices))
    .map((choice) => object(choice).message)
    .filter(Boolean);
  if (chat.length) return chat.map((item) => conversationMessage(item));
  const native = events.filter((event) => event.type === "message" && Array.isArray(event.content));
  if (native.length) return native.map((event) => conversationMessage(event));
  const blocks = new Map<string, ConversationBlock>();
  const append = (key: string, block: ConversationBlock) => {
    const previous = blocks.get(key);
    blocks.set(
      key,
      previous
        ? {
            ...previous,
            ...block,
            text: previous.text + block.text,
            name: block.name || previous.name,
            id: block.id || previous.id,
          }
        : block,
    );
  };
  for (const event of events) {
    const delta = object(event.delta);
    const index = String(event.output_index ?? event.index ?? 0);
    if (event.type === "response.output_item.done") {
      const item = object(event.item);
      const parsed = conversationMessage(item);
      for (const [blockIndex, block] of parsed.blocks.entries()) blocks.set(`responses:${index}:${blockIndex}`, block);
    } else if (event.type === "response.output_text.delta") {
      append(`responses:${index}:${String(event.content_index ?? 0)}`, {
        kind: "text",
        text: String(event.delta ?? ""),
      });
    } else if (event.type === "response.function_call_arguments.delta") {
      append(`responses:${index}:0`, { kind: "tool_call", text: String(event.delta ?? "") });
    } else if (event.type === "response.output_item.added") {
      const item = object(event.item);
      if (["function_call", "custom_tool_call"].includes(String(item.type)))
        blocks.set(`responses:${index}:0`, contentBlocks(item)[0]);
    } else if (event.type === "response.reasoning_summary_text.delta") {
      append(`responses:${index}:${String(event.summary_index ?? 0)}`, {
        kind: "thinking",
        text: String(event.delta ?? ""),
      });
    } else if (event.type === "content_block_start") {
      const block = contentBlocks(event.content_block)[0];
      if (block)
        blocks.set(`anthropic:${index}`, {
          ...block,
          text: block.kind === "tool_call" && block.text === "{}" ? "" : block.text,
        });
    } else if (event.type === "content_block_delta") {
      if (delta.type === "text_delta") append(`anthropic:${index}`, { kind: "text", text: String(delta.text ?? "") });
      if (delta.type === "thinking_delta")
        append(`anthropic:${index}`, { kind: "thinking", text: String(delta.thinking ?? "") });
      if (delta.type === "input_json_delta")
        append(`anthropic:${index}`, { kind: "tool_call", text: String(delta.partial_json ?? "") });
    }
    for (const choice of array(event.choices)) {
      const entry = object(choice);
      const change = object(entry.delta);
      const key = `chat:${String(entry.index ?? 0)}`;
      if (change.content) append(key, { kind: "text", text: readableContent(change.content) });
      if (change.reasoning_content)
        append(`${key}:thinking`, { kind: "thinking", text: String(change.reasoning_content) });
      for (const raw of array(change.tool_calls)) {
        const call = object(raw);
        const fn = object(call.function);
        append(`${key}:tool:${String(call.index ?? 0)}`, {
          kind: "tool_call",
          text: String(fn.arguments ?? ""),
          name: typeof fn.name === "string" ? fn.name : undefined,
          id: typeof call.id === "string" ? call.id : undefined,
        });
      }
    }
  }
  const parsed = [...blocks.values()].map((block) => {
    if (block.kind !== "tool_call") return block;
    try {
      return { ...block, data: JSON.parse(block.text) as unknown };
    } catch {
      return { ...block, data: block.text };
    }
  });
  return parsed.length ? [{ role: "assistant", blocks: parsed }] : [];
}

export function currentConversationInput(messages: ConversationMessage[]): ConversationMessage[] {
  const lastUser = messages.findLastIndex(
    (message) => message.role === "user" && message.blocks.some((block) => block.kind !== "tool_result"),
  );
  return lastUser < 0
    ? messages.filter((message) => !["system", "developer"].includes(message.role))
    : messages.slice(lastUser);
}

export function requestMessages(value: unknown): { role: string; content: string }[] {
  if (Array.isArray(value)) return value.flatMap((event) => requestMessages(object(event).response ?? event));
  const body = object(value);
  const messages = array(body.messages ?? body.input);
  const lastUser = messages.map((item) => object(item).role).lastIndexOf("user");
  const current = lastUser >= 0 ? messages.slice(lastUser) : messages;
  if (current.length)
    return current.map((item) => ({
      role: String(object(item).role ?? "工具"),
      content: readableContent(object(item).content ?? item),
    }));
  if (typeof body.input === "string") return [{ role: "user", content: body.input }];
  if (typeof value === "string")
    return protocolEvents(value).flatMap((event) => requestMessages(event.response ?? event));
  return [];
}

export function requestInstructions(value: unknown): string {
  return protocolEvents(value)
    .flatMap((event) => {
      const body = object(event.response ?? event);
      return [
        body.instructions,
        body.system,
        ...array(body.messages ?? body.input)
          .filter((item) => ["system", "developer"].includes(String(object(item).role)))
          .map((item) => object(item).content),
      ];
    })
    .map(readableContent)
    .filter(Boolean)
    .join("\n\n");
}

export function responseText(value: unknown): string {
  const events = protocolEvents(value);
  const completed = events.map((event) => object(event.response ?? event)).filter((event) => event.output != null);
  const finals = completed.filter((event, index) => completed.findLastIndex((item) => item.id === event.id) === index);
  if (finals.length) return finals.map((event) => readableContent(event.output)).join("\n\n");
  const complete = events
    .flatMap((event) => array(event.choices))
    .map((choice) => object(choice).message)
    .filter(Boolean);
  if (complete.length) return complete.map((message) => readableContent(object(message).content ?? message)).join("\n");
  const chunks = events
    .flatMap((event) => {
      if (event.type === "response.output_text.delta" && typeof event.delta === "string") return [event.delta];
      if (event.type === "content_block_delta") return [String(object(event.delta).text ?? "")];
      return array(event.choices).map((choice) => String(object(object(choice).delta).content ?? ""));
    })
    .join("");
  return chunks || readableContent(object(value).content) || "本次无文本回复；可在原始记录中查看工具调用或错误";
}
