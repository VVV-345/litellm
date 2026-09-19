/** 本文件验证完整会话阅读时的历史去重和不同流式协议的回复提取。 */
import { describe, expect, it } from "vitest";
import {
  currentConversationInput,
  conversationInput,
  conversationOutput,
  requestInstructions,
  requestMessages,
  responseText,
} from "./logConversation";

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

it("preserves native tool arguments across stream chunks without stale empty input", () => {
  const messages = conversationOutput([
    { type: "content_block_start", index: 0, content_block: { type: "thinking", thinking: "" } },
    { type: "content_block_delta", index: 0, delta: { type: "thinking_delta", thinking: "reason" } },
    {
      type: "content_block_start",
      index: 1,
      content_block: { type: "tool_use", name: "lookup", id: "call-1", input: {} },
    },
    { type: "content_block_delta", index: 1, delta: { type: "input_json_delta", partial_json: '{"q":' } },
    { type: "content_block_delta", index: 1, delta: { type: "input_json_delta", partial_json: '"value"}' } },
  ]);
  expect(messages).toEqual([
    {
      role: "assistant",
      blocks: [
        { kind: "thinking", text: "reason", name: undefined, id: undefined },
        { kind: "tool_call", text: '{"q":"value"}', name: "lookup", id: "call-1", data: { q: "value" } },
      ],
    },
  ]);
});

it("preserves chat tool identities and Responses terminal output without duplication", () => {
  expect(
    conversationOutput([
      {
        choices: [
          { delta: { tool_calls: [{ index: 0, id: "call", function: { name: "search", arguments: '{"x":' } }] } },
        ],
      },
      { choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: "1}" } }] } }] },
    ])[0].blocks[0],
  ).toMatchObject({ kind: "tool_call", id: "call", name: "search", data: { x: 1 } });
  const terminal = {
    type: "response.completed",
    response: {
      id: "resp",
      output: [
        { type: "function_call", name: "search", call_id: "call", arguments: '{"x":1}' },
        { type: "message", role: "assistant", content: [{ type: "output_text", text: "done" }] },
      ],
    },
  };
  const output = conversationOutput([{ type: "response.output_text.delta", delta: "done" }, terminal, terminal]);
  expect(output).toHaveLength(2);
  expect(output[0].blocks[0]).toMatchObject({ kind: "tool_call", id: "call", name: "search" });
  expect(output[1].blocks).toEqual([{ kind: "text", text: "done" }]);
});

it("keeps system history and tool results, labels encrypted reasoning without inventing text", () => {
  const messages = conversationInput({
    instructions: "system",
    input: [
      { role: "user", content: "question" },
      { type: "reasoning", encrypted_content: "secret", summary: [] },
      { type: "function_call_output", call_id: "call", output: "result" },
    ],
  });
  expect(messages.map((message) => message.role)).toEqual(["system", "user", "assistant", "tool"]);
  expect(messages[2].blocks[0].kind).toBe("other");
  expect(JSON.stringify(messages)).not.toContain("secret");
  expect(messages[3].blocks[0]).toMatchObject({ kind: "tool_result", id: "call", text: "result" });
});

it("keeps the user question and tool call in the current turn after native tool results", () => {
  const input = conversationInput({
    messages: [
      { role: "user", content: "question" },
      { role: "assistant", content: [{ type: "tool_use", name: "search", id: "call", input: {} }] },
      { role: "user", content: [{ type: "tool_result", tool_use_id: "call", content: "result" }] },
    ],
  });
  expect(currentConversationInput(input)).toHaveLength(3);
  expect(currentConversationInput(input)[0].blocks[0].text).toBe("question");
});
