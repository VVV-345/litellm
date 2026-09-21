"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useAuth } from "./AuthContext";
import { createDashboardQueryClient } from "@/lib/queryClient";
import { DATA_CHANGED_EVENT, SESSION_RESET_EVENT } from "@/lib/cacheEvents";
import { getRequestBaseUrl } from "@/lib/http/runtime";
import ModuleLoading from "@/components/shared/ModuleLoading";
import { usePathname } from "next/navigation";

export default function ReactQueryProvider({ children }: { children: React.ReactNode }) {
  const { authLoading, token, accessToken, userID, userRole } = useAuth();
  const pathname = usePathname();
  const loginPage = /(?:^|\/)login\/?$/.test(pathname ?? "");
  const [baseUrl, setBaseUrl] = useState(getRequestBaseUrl);
  useEffect(() => {
    const reset = () => setBaseUrl(getRequestBaseUrl());
    window.addEventListener(SESSION_RESET_EVENT, reset);
    return () => window.removeEventListener(SESSION_RESET_EVENT, reset);
  }, []);
  const scope = loginPage ? "login" : JSON.stringify([token, accessToken, userID, userRole, baseUrl]);
  if (authLoading && !loginPage) return <ModuleLoading />;
  return <SessionQueryProvider key={scope}>{children}</SessionQueryProvider>;
}

function SessionQueryProvider({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(createDashboardQueryClient);
  useEffect(() => {
    const clear = () => queryClient.clear();
    const invalidate = () => void queryClient.invalidateQueries({ refetchType: "none" });
    window.addEventListener(SESSION_RESET_EVENT, clear);
    window.addEventListener(DATA_CHANGED_EVENT, invalidate);
    return () => {
      window.removeEventListener(SESSION_RESET_EVENT, clear);
      window.removeEventListener(DATA_CHANGED_EVENT, invalidate);
      clear();
    };
  }, [queryClient]);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
