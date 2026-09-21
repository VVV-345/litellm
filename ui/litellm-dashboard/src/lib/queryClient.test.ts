import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./http/client";
import { createDashboardQueryClient } from "./queryClient";

describe("dashboard query caching", () => {
  it("deduplicates concurrent requests and reuses fresh data until invalidated", async () => {
    const client = createDashboardQueryClient();
    const queryFn = vi.fn().mockResolvedValueOnce("before").mockResolvedValue("after");
    const options = { queryKey: ["list"], queryFn };
    expect(await Promise.all([client.fetchQuery(options), client.fetchQuery(options)])).toEqual(["before", "before"]);
    expect(await client.fetchQuery(options)).toBe("before");
    expect(queryFn).toHaveBeenCalledTimes(1);
    await client.invalidateQueries({ queryKey: ["list"] });
    expect(await client.fetchQuery(options)).toBe("after");
    expect(queryFn).toHaveBeenCalledTimes(2);
    client.clear();
  });

  it("respects per-query freshness overrides", async () => {
    const client = createDashboardQueryClient();
    const queryFn = vi.fn().mockResolvedValue("value");
    const options = { queryKey: ["live"], queryFn, staleTime: 0 };
    await client.fetchQuery(options);
    await client.fetchQuery(options);
    expect(queryFn).toHaveBeenCalledTimes(2);
    client.clear();
  });

  it.each([401, 403])("does not retry authorization failure %s", async (status) => {
    const client = createDashboardQueryClient();
    const queryFn = vi.fn().mockRejectedValue(new ApiError("denied", status, null));
    await expect(client.fetchQuery({ queryKey: ["private"], queryFn })).rejects.toThrow("denied");
    expect(queryFn).toHaveBeenCalledTimes(1);
    client.clear();
  });

  it("does not restore cleared data when an old session request completes", async () => {
    const client = createDashboardQueryClient();
    let resolve!: (value: string) => void;
    const request = client.fetchQuery({
      queryKey: ["private"],
      queryFn: () =>
        new Promise<string>((done) => {
          resolve = done;
        }),
    });
    const rejected = expect(request).rejects.toBeDefined();
    client.clear();
    resolve("old account");
    await rejected;
    expect(client.getQueryData(["private"])).toBeUndefined();
  });
});
