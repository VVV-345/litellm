import type { QueryClient } from "@tanstack/react-query";

let client: QueryClient | null = null;
const consumersByClient = new WeakMap<QueryClient, Map<string, number>>();

const clientConsumers = (sessionClient: QueryClient): Map<string, number> => {
  const existing = consumersByClient.get(sessionClient);
  if (existing) return existing;
  const created = new Map<string, number>();
  consumersByClient.set(sessionClient, created);
  return created;
};

export function registerResponseCache(next: QueryClient): () => void {
  client = next;
  return () => {
    if (client === next) client = null;
  };
}

export const cachedDashboardRequest = async (
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  send: typeof fetch,
): Promise<Response> => {
  const request = input instanceof Request ? input : null;
  const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
  const sessionClient = client;
  const cache = init?.cache ?? request?.cache;
  const signal = init?.signal ?? request?.signal;
  signal?.throwIfAborted();
  const headers = new Headers(init?.headers ?? request?.headers);
  const target = new URL(request?.url ?? String(input), globalThis.location?.origin ?? "http://localhost");
  if (
    !sessionClient ||
    method !== "GET" ||
    ["reload", "no-cache", "no-store"].includes(cache ?? "") ||
    /(?:^|\/)(?:auth-token|oauth|login|logout|authorize|auth-url|auth-status)(?:\/|$)/.test(target.pathname) ||
    headers.get("accept")?.includes("text/event-stream")
  )
    return send(input, init);
  target.searchParams.sort();
  const url = target.toString();
  const options = {
    queryKey: [
      "dashboard-http",
      url,
      Array.from(headers.entries())
        .filter(([key]) => key !== "content-type")
        .sort(),
    ] as const,
    queryFn: async ({ signal: querySignal }: { signal: AbortSignal }) => {
      const response = await send(input, { ...init, signal: querySignal });
      if (!response.ok || !response.headers.get("content-type")?.includes("json")) throw response;
      return {
        body: await response.arrayBuffer(),
        status: response.status,
        statusText: response.statusText,
        headers: Array.from(response.headers.entries()),
      };
    },
    staleTime: 0,
    gcTime: 0,
    structuralSharing: false,
    retry: false,
  };
  const consumers = clientConsumers(sessionClient);
  const consumerKey = JSON.stringify(options.queryKey);
  consumers.set(consumerKey, (consumers.get(consumerKey) ?? 0) + 1);
  let released = false;
  const release = (cancelIfLast: boolean) => {
    if (released) return;
    released = true;
    const remaining = (consumers.get(consumerKey) ?? 1) - 1;
    if (remaining <= 0) {
      consumers.delete(consumerKey);
      if (cancelIfLast) void sessionClient.cancelQueries({ queryKey: options.queryKey, exact: true });
      return;
    }
    consumers.set(consumerKey, remaining);
  };

  const fetchPromise = sessionClient.fetchQuery(options);
  void fetchPromise.catch(() => {});
  let onAbort: (() => void) | undefined;
  try {
    const response = signal
      ? await Promise.race([
          fetchPromise,
          new Promise<never>((_, reject) => {
            onAbort = () => {
              release(true);
              reject(signal.reason ?? new DOMException("The operation was aborted.", "AbortError"));
            };
            signal.addEventListener("abort", onAbort, { once: true });
            if (signal.aborted) onAbort();
          }),
        ])
      : await fetchPromise;
    signal?.throwIfAborted();
    return new Response(response.body.slice(0), response);
  } catch (error) {
    if (error instanceof Response) return error.clone();
    throw error;
  } finally {
    if (onAbort) signal?.removeEventListener("abort", onAbort);
    release(false);
  }
};
