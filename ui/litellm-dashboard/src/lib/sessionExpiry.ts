import { clearTokenCookies, getCookie } from "@/utils/cookieUtils";

const EXPIRED_KEY_MESSAGE = "Authentication Error - Expired Key";
const PROVIDER_AUTH = /[A-Za-z0-9_]+Exception:\s*Authentication Error/;

export interface SessionFailure {
  message: string;
  status?: number;
  sessionToken?: string | null;
}

let settledToken: string | null | undefined;

export function isUiSessionExpiry(failure: SessionFailure): boolean {
  if (failure.status === 403) return false;
  if (failure.status !== undefined && failure.status !== 401) return false;
  if (PROVIDER_AUTH.test(failure.message)) return false;
  return failure.message.includes(EXPIRED_KEY_MESSAGE);
}

export function expireUiSession(failure: SessionFailure): boolean {
  if (!isUiSessionExpiry(failure)) return false;
  const current = getCookie("token");
  if (failure.sessionToken !== undefined && current !== failure.sessionToken) return false;
  if (settledToken !== undefined && (current === null || current === settledToken)) return false;
  settledToken = current;
  clearTokenCookies();
  if (typeof window !== "undefined") {
    window.location.href = window.location.pathname;
  }
  return true;
}

export function unauthorizedSessionAction(
  currentToken: string | null,
  renderedToken: string | null,
  currentValid: boolean,
  adminDisabled: boolean,
): "allow" | "clear-and-redirect" | "redirect" {
  if (currentValid && !adminDisabled) return "allow";
  if (currentToken && currentToken !== renderedToken && currentValid) return "redirect";
  if (currentToken) return "clear-and-redirect";
  return "redirect";
}
