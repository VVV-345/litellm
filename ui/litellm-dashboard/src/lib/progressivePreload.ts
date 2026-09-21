type Preload = () => unknown | Promise<unknown>;

export function schedulePreloads(loaders: readonly Preload[]): () => void {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } })
    .connection;
  if (connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType ?? "")) return () => {};

  let index = 0;
  let stopped = false;
  let running = false;
  let timer: number | undefined;
  let idle: number | undefined;

  const cancelScheduled = () => {
    window.clearTimeout(timer);
    if (idle !== undefined) window.cancelIdleCallback(idle);
    timer = undefined;
    idle = undefined;
  };
  const schedule = () => {
    if (stopped || document.hidden || running || index >= loaders.length || timer !== undefined || idle !== undefined)
      return;
    timer = window.setTimeout(() => {
      timer = undefined;
      if (typeof window.requestIdleCallback === "function") idle = window.requestIdleCallback(run);
      else void run();
    }, 1500);
  };
  const run = async () => {
    idle = undefined;
    if (stopped || document.hidden) return;
    running = true;
    await Promise.allSettled([Promise.resolve().then(loaders[index++])]);
    running = false;
    schedule();
  };
  const onVisibilityChange = () => {
    if (document.hidden) cancelScheduled();
    else schedule();
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  schedule();
  return () => {
    stopped = true;
    cancelScheduled();
    document.removeEventListener("visibilitychange", onVisibilityChange);
  };
}
