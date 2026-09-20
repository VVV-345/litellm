/** 验证版本操作能刷新所有版本的说明缓存，并保留不同登录会话的缓存隔离。 */
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

describe("release command cache invalidation", () => {
  it("invalidates all versions for the current token without invalidating another session", async () => {
    const client = new QueryClient();
    const first = accountPoolQueryKeys.releaseCommands("current", "first");
    const second = accountPoolQueryKeys.releaseCommands("current", "second");
    const otherSession = accountPoolQueryKeys.releaseCommands("other", "first");
    client.setQueryData(first, "first command");
    client.setQueryData(second, "second command");
    client.setQueryData(otherSession, "other command");

    await client.invalidateQueries({ queryKey: accountPoolQueryKeys.releaseCommandsRoot("current") });

    expect(client.getQueryState(first)?.isInvalidated).toBe(true);
    expect(client.getQueryState(second)?.isInvalidated).toBe(true);
    expect(client.getQueryState(otherSession)?.isInvalidated).toBe(false);
    client.clear();
  });
});
