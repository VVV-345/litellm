import { notifyDashboardDataChanged } from "../cacheEvents";
import { cachedDashboardRequest } from "./responseCache";

export const dashboardFetch: typeof fetch = async (input, init) => {
  const cache = input instanceof Request && input.cache !== "default" ? input.cache : "no-store";
  const response = await cachedDashboardRequest(input, init, (target, options) =>
    globalThis.fetch(target, { cache, ...options }),
  );
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  if (response.ok && !["GET", "HEAD", "OPTIONS"].includes(method)) notifyDashboardDataChanged();
  return response;
};
