/** 本文件验证完整会话阅读时的历史去重和不同流式协议的回复提取。 */
import { describe, expect, it } from "vitest";
import { requestInstructions, requestMessages, responseText } from "./AccountPoolConversation";

describe("account pool conversations", () => {
  it("keeps every WebSocket response without duplicate terminal frames and reads developer prompts", () => {
    expect(
      responseText([
        { response: { id: "one", output: [{ content: "first answer" }] } },
        { response: { id: "two", output: [{ content: "second answer" }] } },
        { response: { id: "two", output: [{ content: "second answer" }] } },
      ]),
    ).toBe("first answer\n\nsecond answer");
    expect(
      requestInstructions({
        messages: [
          { role: "system", content: "system prompt" },
          { role: "developer", content: "developer prompt" },
          { role: "user", content: "question" },
        ],
      }),
    ).toBe("system prompt\n\ndeveloper prompt");
  });
  it("shows only the current turn while keeping tool results", () => {
    expect(
      requestMessages({
        messages: [
          { role: "system", content: "system instruction" },
          { role: "user", content: "old question" },
          { role: "assistant", content: "old answer" },
          { role: "user", content: "new question" },
          { role: "tool", content: "tool output" },
        ],
      }),
    ).toEqual([
      { role: "user", content: "new question" },
      { role: "tool", content: "tool output" },
    ]);
  });
  it("joins partial chat output and prefers final Responses output without duplication", () => {
    expect(
      responseText(
        'data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: {"choices":[{"delta":{"content":"世界"}}]}\n\n',
      ),
    ).toBe("你好世界");
    expect(
      responseText(
        'data: {"type":"response.output_text.delta","delta":"你好"}\n\ndata: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"你好世界"}]}]}}\n\n',
      ),
    ).toBe("你好世界");
  });
  it("reads native message deltas and plain response input", () => {
    expect(responseText('data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"回复"}}\n\n')).toBe(
      "回复",
    );
    expect(requestMessages({ input: "输入" })).toEqual([{ role: "user", content: "输入" }]);
  });
});
