import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { schedulePreloads } from "./progressivePreload";

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
});
