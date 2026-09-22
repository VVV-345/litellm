import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./http/client";

export function createDashboardQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: Infinity,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
        retry: (count, error) => !(error instanceof ApiError && [401, 403].includes(error.status)) && count < 1,
      },
      mutations: { retry: false },
    },
  });
}
