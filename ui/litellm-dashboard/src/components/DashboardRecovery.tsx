"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { fetchDashboardBuildId } from "@/lib/http/client";
import { confirmDiscardChanges } from "@/lib/useUnsavedChanges";
import { canChunkReload, isModuleLoadError, MODULE_ERROR_EVENT, takeChunkReload } from "@/lib/dashboardRecovery";

const recoveryMessage = (capped: boolean, newBuild: boolean): string => {
  if (capped) return "dashboardRecovery.reloadLimited";
  if (newBuild) return "dashboardRecovery.newVersion";
  return "dashboardRecovery.moduleFailed";
};

export function updateDashboard() {
  if (!canChunkReload()) return;
  if (!confirmDiscardChanges()) return;
  if (!takeChunkReload()) return;
  window.location.reload();
}

export default function DashboardRecovery() {
  const { t } = useTranslation();
  const [newBuild, setNewBuild] = useState(false);
  const [failed, setFailed] = useState(false);
  const [capped, setCapped] = useState(() => !canChunkReload());
  const reload = () => {
    updateDashboard();
    if (!canChunkReload()) setCapped(true);
  };
  useEffect(() => {
    let lastCheck = -Infinity;
    let pending: AbortController | undefined;
    let stopped = false;
    const checkBuild = async () => {
      const current = document.querySelector<HTMLMetaElement>('meta[name="litellm-ui-build"]')?.content;
      if (!current || document.hidden || pending || Date.now() - lastCheck < 60_000) return;
      lastCheck = Date.now();
      const controller = new AbortController();
      pending = controller;
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const latest = await fetchDashboardBuildId(window.location.href, controller.signal);
        if (!stopped && latest && latest !== current) setNewBuild(true);
      } catch {
        // Offline checks must not turn into deployment warnings.
      } finally {
        window.clearTimeout(timeout);
        pending = undefined;
      }
    };
    const onModuleError = () => setFailed(true);
    const onError = (event: ErrorEvent) => {
      const target = event.target;
      if (
        isModuleLoadError(event.error ?? event.message) ||
        (target instanceof HTMLScriptElement && target.src.includes("/_next/"))
      )
        onModuleError();
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      if (isModuleLoadError(event.reason)) onModuleError();
    };
    const onNavigate = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (
        !(link instanceof HTMLAnchorElement) ||
        link.target === "_blank" ||
        link.hasAttribute("download") ||
        link.href === location.href
      )
        return;
      if (!confirmDiscardChanges()) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    document.addEventListener("visibilitychange", checkBuild);
    window.addEventListener("pageshow", checkBuild);
    window.addEventListener("error", onError, true);
    window.addEventListener("unhandledrejection", onRejection);
    window.addEventListener(MODULE_ERROR_EVENT, onModuleError);
    document.addEventListener("click", onNavigate, true);
    return () => {
      stopped = true;
      pending?.abort();
      document.removeEventListener("visibilitychange", checkBuild);
      window.removeEventListener("pageshow", checkBuild);
      window.removeEventListener("error", onError, true);
      window.removeEventListener("unhandledrejection", onRejection);
      window.removeEventListener(MODULE_ERROR_EVENT, onModuleError);
      document.removeEventListener("click", onNavigate, true);
    };
  }, []);

  if (!newBuild && !failed) return null;
  return (
    <aside role="alert" className="fixed top-2 right-2 z-toast max-w-md rounded-lg border bg-background p-4 shadow-lg">
      <p>{t(recoveryMessage(capped, newBuild))}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t("dashboardRecovery.keepDraft")}</p>
      {!capped && (
        <div className="mt-3 flex gap-2">
          {failed && (
            <Button variant="outline" onClick={reload}>
              {t("dashboardRecovery.retry")}
            </Button>
          )}
          <Button onClick={reload}>{t("dashboardRecovery.update")}</Button>
        </div>
      )}
    </aside>
  );
}
