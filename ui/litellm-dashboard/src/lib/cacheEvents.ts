export const SESSION_RESET_EVENT = "litellm:session-reset";
export const DATA_CHANGED_EVENT = "litellm:data-changed";

export function notifyDashboardDataChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(DATA_CHANGED_EVENT));
}

export function resetDashboardSession() {
  if (typeof window === "undefined") return;
  try {
    Object.keys(sessionStorage)
      .filter((key) => key === "possibleUserRoles" || key.startsWith("userModels") || key.startsWith("userSpendData"))
      .forEach((key) => sessionStorage.removeItem(key));
  } catch {
    // Storage may be unavailable; the in-memory cache still needs clearing.
  }
  window.dispatchEvent(new Event(SESSION_RESET_EVENT));
}
