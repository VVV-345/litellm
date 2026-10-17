"use client";

import { useEffect } from "react";
import i18n from "@/i18n";

const drafts = new Set<symbol>();

export const confirmDiscardChanges = () =>
  drafts.size === 0 || window.confirm(i18n.t("dashboardRecovery.discardChanges"));

export function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const draft = Symbol();
    drafts.add(draft);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      drafts.delete(draft);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [dirty]);
}
