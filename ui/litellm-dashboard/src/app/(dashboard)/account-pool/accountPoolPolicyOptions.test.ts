import { describe, expect, it } from "vitest";

import type { PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { buildAccountPoolPolicyOptions } from "./accountPoolPolicyOptions";

const environment = (
  id: string,
  name: string,
  supplier: AccountPoolEnvironment["supplier"],
  models: string[],
): AccountPoolEnvironment =>
  ({
    id,
    name,
    supplier,
    channel: "cliproxyapi",
    available_models: models,
    enabled_models: models,
  }) as AccountPoolEnvironment;

describe("buildAccountPoolPolicyOptions", () => {
  it("reuses groups, tags and models while limiting account references to the same provider", () => {
    const current = environment("current", "Current", "openai_codex", ["gpt-5"]);
    const peer = environment("peer", "Peer Codex", "openai_codex", ["gpt-5-codex"]);
    const unrelated = environment("other", "Claude", "anthropic_claude", ["claude-sonnet"]);
    const policies = [
      {
        card_id: peer.id,
        policy: {
          group: "production",
          tags: ["premium", "production"],
          excluded_models: ["gpt-4.1"],
          model_aliases: [{ alias: "coding", target: "gpt-5-codex" }],
          codex: { allow_app_server_clients: ["codex_app", "desktop"] },
          antigravity: { sensitive_words: ["secret"] },
        },
      },
      {
        card_id: unrelated.id,
        policy: {
          group: "research",
          tags: ["external"],
          excluded_models: ["claude-opus"],
          model_aliases: [],
        },
      },
    ] as PolicyView[];

    const options = buildAccountPoolPolicyOptions(current, [current, peer, unrelated], policies);

    expect(options.groups).toEqual(["production", "research"]);
    expect(options.tags.map((option) => option.value)).toEqual(["external", "premium", "production"]);
    expect(options.models.map((option) => option.value)).toEqual(["coding", "gpt-4.1", "gpt-5", "gpt-5-codex"]);
    expect(options.accounts).toEqual([{ label: "Peer Codex", value: "peer", description: "peer" }]);
    expect(options.codexAppServerClients.map((option) => option.value)).toEqual(["codex_app", "desktop"]);
    expect(options.antigravitySensitiveWords.map((option) => option.value)).toEqual(["secret"]);
  });
});
