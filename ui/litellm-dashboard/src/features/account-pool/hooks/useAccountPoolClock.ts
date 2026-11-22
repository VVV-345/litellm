import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();
let now = Date.now();
let timer: number | null = null;

const notify = () => {
  now = Date.now();
  listeners.forEach((listener) => listener());
};

const subscribe = (listener: () => void) => {
  const wasIdle = timer === null;
  listeners.add(listener);
  if (wasIdle && typeof window !== "undefined") {
    now = Date.now();
    timer = window.setInterval(notify, 60_000);
    queueMicrotask(() => {
      if (listeners.has(listener)) listener();
    });
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };
};

export const useAccountPoolClock = (): number =>
  useSyncExternalStore(
    subscribe,
    () => now,
    () => 0,
  );
