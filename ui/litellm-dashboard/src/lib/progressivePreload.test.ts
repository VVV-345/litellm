import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { runPreloadStages, schedulePreloads } from "./progressivePreload";

describe("progressive preloading", () => {
  const page = Object.assign(new EventTarget(), { hidden: false });
  beforeEach(() => {
    vi.useFakeTimers();
    page.hidden = false;
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { setTimeout, clearTimeout });
    vi.stubGlobal("navigator", { connection: {} });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("spaces loads and waits for an in-flight module before starting the next", async () => {
    const first = Promise.withResolvers<void>();
    const load = vi.fn(() => first.promise);
    const next = vi.fn();
    const stop = schedulePreloads([load, next]);
    await vi.advanceTimersByTimeAsync(1499);
    expect(load).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(load).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(10000);
    expect(next).not.toHaveBeenCalled();
    first.resolve();
    await vi.advanceTimersByTimeAsync(1500);
    expect(next).toHaveBeenCalledOnce();
    stop();
  });

  it("waits for browser idle time when supported and cancels queued idle work", async () => {
    const idle = vi.fn(() => 4);
    const cancel = vi.fn();
    vi.stubGlobal("window", { setTimeout, clearTimeout, requestIdleCallback: idle, cancelIdleCallback: cancel });
    const load = vi.fn();
    const stop = schedulePreloads([load]);
    await vi.advanceTimersByTimeAsync(1500);
    expect(idle).toHaveBeenCalledOnce();
    expect(load).not.toHaveBeenCalled();
    stop();
    expect(cancel).toHaveBeenCalledWith(4);
  });

  it("pauses in a background tab and continues when visible", async () => {
    const load = vi.fn();
    const stop = schedulePreloads([load]);
    page.hidden = true;
    page.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).not.toHaveBeenCalled();
    page.hidden = false;
    page.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(1500);
    expect(load).toHaveBeenCalledOnce();
    stop();
  });

  it("continues after a failed preload and stops pending work on cleanup", async () => {
    const next = vi.fn();
    const last = vi.fn();
    const stop = schedulePreloads([() => Promise.reject(new Error("offline")), next, last]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(next).toHaveBeenCalledOnce();
    stop();
    await vi.advanceTimersByTimeAsync(3000);
    expect(last).not.toHaveBeenCalled();
  });

  it.each([{ saveData: true }, { effectiveType: "2g" }])("respects connection constraints: %s", async (connection) => {
    vi.stubGlobal("navigator", { connection });
    const load = vi.fn();
    const stop = schedulePreloads([load]);
    await vi.advanceTimersByTimeAsync(10000);
    expect(load).not.toHaveBeenCalled();
    stop();
  });

  it("finishes primary tasks before starting background batches and reports progress", async () => {
    const events: string[] = [];
    const progress: Array<{ stage: string; primary: number; background: number }> = [];
    const result = await runPreloadStages(
      [
        { id: "keys", run: () => events.push("keys") },
        { id: "models", run: () => events.push("models") },
      ],
      [[{ id: "logs", run: () => events.push("logs") }]],
      {
        onProgress: (state) =>
          progress.push({
            stage: state.stage,
            primary: state.primary.completed,
            background: state.background.completed,
          }),
        yieldBetweenTasks: async () => {},
      },
    );

    expect(events.slice(0, 2)).toEqual(expect.arrayContaining(["keys", "models"]));
    expect(events[2]).toBe("logs");
    expect(
      progress.some((entry) => entry.stage === "background" && entry.primary === 2 && entry.background === 0),
    ).toBe(true);
    expect(result).toMatchObject({ stage: "complete", primary: { completed: 2 }, background: { completed: 1 } });
    expect(progress.at(-1)).toEqual({ stage: "complete", primary: 2, background: 1 });
  });

  it("keeps the primary screen blocked when a required task fails", async () => {
    const laterTask = vi.fn();
    const result = await runPreloadStages(
      [{ id: "failed", run: () => Promise.reject(new Error("offline")) }],
      [[{ id: "later", run: laterTask }]],
      { yieldBetweenTasks: async () => {} },
    );

    expect(laterTask).not.toHaveBeenCalled();
    expect(result.stage).toBe("primary");
    expect(result.primary.failed).toBe(1);
    expect(result.background.failed).toBe(0);
  });

  it("stops queued work after logout while a request is in flight", async () => {
    const controller = new AbortController();
    const pending = Promise.withResolvers<void>();
    const later = vi.fn();
    const finished = runPreloadStages(
      [{ id: "pending", run: () => pending.promise }],
      [[{ id: "later", run: later }]],
      { signal: controller.signal, yieldBetweenTasks: async () => {} },
    );
    controller.abort();
    pending.resolve();
    await finished;
    expect(later).not.toHaveBeenCalled();
  });

  it("discovers and loads subsequent log pages automatically after the primary pages", async () => {
    const events: string[] = [];
    const result = await runPreloadStages([{ id: "logs:1-3", run: () => events.push("first-three") }], [], {
      yieldBetweenTasks: async () => {},
      loadBackground: async () => {
        events.push("discover");
        return [
          [{ id: "logs:4", run: () => events.push("page-four") }],
          [{ id: "logs:5", run: () => events.push("page-five") }],
        ];
      },
    });
    expect(events).toEqual(["first-three", "discover", "page-four", "page-five"]);
    expect(result.background).toEqual({ completed: 2, total: 2, failed: 0 });
  });

  it("reports failed background discovery without an unhandled rejection or a false success", async () => {
    const progress = vi.fn();
    const result = await runPreloadStages([], [], {
      onProgress: progress,
      loadBackground: async () => {
        throw new Error("offline");
      },
    });
    expect(result.background).toEqual({ completed: 1, total: 1, failed: 1 });
    expect(progress).toHaveBeenLastCalledWith(result);
  });
});
