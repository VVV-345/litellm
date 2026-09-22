import type { QueryClient } from "@tanstack/react-query";

let client: QueryClient | null = null;

export function registerResponseCache(next: QueryClient): () => void {
  client = next;
  const unsubscribe = next.getQueryCache().subscribe((event) => {
    if (event.type !== "updated" || event.query.queryKey[0] === "dashboard-http") return;
    if (event.action.type === "fetch" && event.query.state.data !== undefined) {
      void next.invalidateQueries({ queryKey: ["dashboard-http"], refetchType: "none" });
    }
  });
  return () => {
    unsubscribe();
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
    staleTime: 5 * 60_000,
    gcTime: Infinity,
    structuralSharing: false,
    retry: false,
  };
  try {
    const cached = sessionClient.getQueryData<Awaited<ReturnType<typeof options.queryFn>>>(options.queryKey);
    if (cached && !sessionClient.getQueryState(options.queryKey)?.isInvalidated) {
      void sessionClient.fetchQuery(options).catch(() => {});
      return new Response(cached.body.slice(0), cached);
    }
    const response = await sessionClient.fetchQuery(options);
    signal?.throwIfAborted();
    return new Response(response.body.slice(0), response);
  } catch (error) {
    if (error instanceof Response) return error.clone();
    throw error;
  }
};
