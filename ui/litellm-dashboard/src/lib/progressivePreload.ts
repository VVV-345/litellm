type Preload = () => unknown | Promise<unknown>;

export interface PreloadTask {
  id: string;
  run: Preload;
}

export interface PreloadProgress {
  completed: number;
  total: number;
  failed: number;
}

export interface PreloadStageProgress {
  primary: PreloadProgress;
  background: PreloadProgress;
  stage: "primary" | "background" | "complete";
}

export interface RunPreloadStagesOptions {
  signal?: AbortSignal;
  loadBackground?: () => Promise<readonly (readonly PreloadTask[])[]>;
  onProgress?: (progress: PreloadStageProgress) => void;
  isVisible?: () => boolean;
  waitForVisible?: () => Promise<void>;
  yieldBetweenTasks?: () => Promise<void>;
}

const emptyProgress = (total: number): PreloadProgress => ({ completed: 0, total, failed: 0 });

const defaultYield = () => new Promise<void>((resolve) => window.setTimeout(resolve, 250));

const defaultIsVisible = () => !document.hidden;

const waitForVisible = async (isVisible: () => boolean, signal?: AbortSignal) => {
  if (isVisible()) return;
  await new Promise<void>((resolve) => {
    const finish = () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      signal?.removeEventListener("abort", finish);
      resolve();
    };
    const onVisibilityChange = () => {
      if (!isVisible()) return;
      finish();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    signal?.addEventListener("abort", finish, { once: true });
    if (signal?.aborted) finish();
  });
};

export async function runPreloadStages(
  primaryTasks: readonly PreloadTask[],
  backgroundBatches: readonly (readonly PreloadTask[])[],
  options: RunPreloadStagesOptions = {},
): Promise<PreloadStageProgress> {
  const isVisible = options.isVisible ?? defaultIsVisible;
  const waitUntilVisible = options.waitForVisible ?? (() => waitForVisible(isVisible, options.signal));
  const yieldBetweenTasks = options.yieldBetweenTasks ?? defaultYield;
  let primary = emptyProgress(primaryTasks.length);
  const backgroundTotal = backgroundBatches.reduce((total, batch) => total + batch.length, 0);
  let background = emptyProgress(backgroundTotal);
  let progress: PreloadStageProgress = { primary, background, stage: "primary" };
  await Promise.resolve();
  if (options.signal?.aborted) return progress;
  options.onProgress?.(progress);

  const runTask = async (task: PreloadTask, target: "primary" | "background") => {
    if (options.signal?.aborted) return;
    await waitUntilVisible();
    if (options.signal?.aborted) return;
    let failed = false;
    try {
      await task.run();
    } catch {
      failed = true;
    }
    if (options.signal?.aborted) return;
    const current = target === "primary" ? primary : background;
    const next: PreloadProgress = {
      completed: current.completed + 1,
      total: current.total,
      failed: current.failed + (failed ? 1 : 0),
    };
    if (target === "primary") primary = next;
    else background = next;
    progress = { primary, background, stage: progress.stage };
    options.onProgress?.(progress);
    await yieldBetweenTasks();
  };

  await Promise.all(primaryTasks.map((task) => runTask(task, "primary")));
  if (options.signal?.aborted || primary.failed > 0) return progress;
  progress = { primary, background, stage: "background" };
  options.onProgress?.(progress);

  let additionalBatches: readonly (readonly PreloadTask[])[] = [];
  try {
    additionalBatches = options.loadBackground ? await options.loadBackground() : [];
  } catch {
    if (options.signal?.aborted) return progress;
    progress = { primary, background: { completed: 1, total: 1, failed: 1 }, stage: "complete" };
    options.onProgress?.(progress);
    return progress;
  }
  if (options.signal?.aborted) return progress;
  background = emptyProgress(backgroundTotal + additionalBatches.reduce((total, batch) => total + batch.length, 0));
  progress = { primary, background, stage: "background" };
  options.onProgress?.(progress);

  for (const batch of [...backgroundBatches, ...additionalBatches]) {
    if (options.signal?.aborted) return progress;
    await Promise.all(batch.map((task) => runTask(task, "background")));
  }

  progress = { primary, background, stage: "complete" };
  options.onProgress?.(progress);
  return progress;
}

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
