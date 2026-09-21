import { notifyDashboardDataChanged } from "../cacheEvents";

export const dashboardFetch: typeof fetch = async (input, init) => {
  const cache = input instanceof Request && input.cache !== "default" ? input.cache : "no-store";
  const response = await globalThis.fetch(input, { cache, ...init });
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  if (response.ok && !["GET", "HEAD", "OPTIONS"].includes(method)) notifyDashboardDataChanged();
  return response;
};
