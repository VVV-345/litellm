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
  return value.split(/\r?\n/).flatMap((line) => {
    const raw = line.startsWith("data:") ? line.slice(5).trim() : line.trim();
    try {
      return [object(JSON.parse(raw))];
    } catch {
      return [];
    }
  });
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
