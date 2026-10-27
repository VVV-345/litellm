import { afterEach, describe, expect, it, vi } from "vitest";
import { clearTokenCookies, getCookie } from "@/utils/cookieUtils";
import { expireUiSession, isUiSessionExpiry, unauthorizedSessionAction } from "./sessionExpiry";

vi.mock("@/utils/cookieUtils", () => ({
  clearTokenCookies: vi.fn(),
  getCookie: vi.fn(),
}));

const expired = "Authentication Error - Expired Key. Key Expiry time 1 and current time 2";

describe("ui session expiry", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("logs out once for an expired UI key and leaves a replacement session in place", () => {
    vi.mocked(getCookie).mockReturnValue("session-a");
    expect(expireUiSession({ message: expired, status: 401, sessionToken: "session-a" })).toBe(true);
    expect(expireUiSession({ message: expired, status: 401, sessionToken: "session-a" })).toBe(false);
    expect(clearTokenCookies).toHaveBeenCalledOnce();

    vi.mocked(getCookie).mockReturnValue("session-b");
    expect(expireUiSession({ message: expired, status: 401, sessionToken: "session-a" })).toBe(false);
    expect(clearTokenCookies).toHaveBeenCalledOnce();
  });

  it("does not log out on 403 or a provider authentication error", () => {
    vi.mocked(getCookie).mockReturnValue("session-a");
    expect(isUiSessionExpiry({ message: expired, status: 403, sessionToken: "session-a" })).toBe(false);
    expect(
      expireUiSession({
        message: "AnthropicException: Authentication Error - Expired Key",
        status: 401,
        sessionToken: "session-a",
      }),
    ).toBe(false);
    expect(expireUiSession({ message: "no access", status: 403, sessionToken: "session-a" })).toBe(false);
    expect(clearTokenCookies).not.toHaveBeenCalled();
  });

  it("keeps a newer valid cookie when the rendered token is already dead", () => {
    expect(unauthorizedSessionAction("session-b", "session-a", true, false)).toBe("allow");
    expect(unauthorizedSessionAction("session-a", "session-a", false, false)).toBe("clear-and-redirect");
    expect(unauthorizedSessionAction(null, null, false, false)).toBe("redirect");
    expect(unauthorizedSessionAction("session-a", "session-a", true, true)).toBe("clear-and-redirect");
    expect(unauthorizedSessionAction("session-b", "session-a", true, true)).toBe("redirect");
  });
});