import { useQueryClient } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { notifyDashboardDataChanged, resetDashboardSession } from "@/lib/cacheEvents";
import { clearTokenCookies } from "@/utils/cookieUtils";
import { registerBaseUrlGetter } from "@/lib/http/runtime";
import ReactQueryProvider from "./ReactQueryProvider";

const auth = { token: "session-a", accessToken: "key-a", userID: "user", userRole: "Admin" };
const route = { pathname: "/ui/api-keys" };
vi.mock("./AuthContext", () => ({ useAuth: () => auth }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));

describe("query session boundary", () => {
  beforeEach(() => {
    auth.token = "session-a";
    route.pathname = "/ui/api-keys";
    registerBaseUrlGetter(() => "");
  });

  it("drops cached records when the session changes even for the same user", () => {
    const clients: ReturnType<typeof useQueryClient>[] = [];
    function Consumer() {
      clients.push(useQueryClient());
      return null;
    }
    const { rerender } = render(
      <ReactQueryProvider>
        <Consumer />
      </ReactQueryProvider>,
    );
    const previous = clients.at(-1)!;
    previous.setQueryData(["private"], "old records");
    auth.token = "session-b";
    rerender(
      <ReactQueryProvider>
        <Consumer />
      </ReactQueryProvider>,
    );
    const current = clients.at(-1)!;
    expect(current).not.toBe(previous);
    expect(current.getQueryData(["private"])).toBeUndefined();
    expect(previous.getQueryData(["private"])).toBeUndefined();
  });

  it("marks saved data stale and resets the client on logout or worker switch", () => {
    let client!: ReturnType<typeof useQueryClient>;
    function Consumer() {
      client = useQueryClient();
      return null;
    }
    render(
      <ReactQueryProvider>
        <Consumer />
      </ReactQueryProvider>,
    );
    client.setQueryData(["list"], "before");
    act(() => notifyDashboardDataChanged());
    expect(client.getQueryState(["list"])?.isInvalidated).toBe(true);
    const previous = client;
    sessionStorage.setItem("userModelsuser", "old models");
    sessionStorage.setItem("possibleUserRoles", "old roles");
    sessionStorage.setItem("excludeInternalHealthChecks", "true");
    registerBaseUrlGetter(() => "https://worker.test");
    act(() => resetDashboardSession());
    expect(client).not.toBe(previous);
    expect(client.getQueryData(["list"])).toBeUndefined();
    expect(previous.getQueryData(["list"])).toBeUndefined();
    expect(sessionStorage.getItem("userModelsuser")).toBeNull();
    expect(sessionStorage.getItem("possibleUserRoles")).toBeNull();
    expect(sessionStorage.getItem("excludeInternalHealthChecks")).toBe("true");
    client.setQueryData(["list"], "worker records");
    act(() => clearTokenCookies());
    expect(client.getQueryData(["list"])).toBeUndefined();
  });

  it("does not remount an anonymous login page when it clears an already empty session", () => {
    auth.token = "";
    route.pathname = "/ui/login/";
    const mounted = vi.fn();
    let client!: ReturnType<typeof useQueryClient>;
    function Login() {
      client = useQueryClient();
      mounted();
      return null;
    }
    render(
      <ReactQueryProvider>
        <Login />
      </ReactQueryProvider>,
    );
    const previous = client;
    act(() => clearTokenCookies());
    expect(client).toBe(previous);
    expect(mounted).toHaveBeenCalledTimes(1);
  });

  it("keeps login callbacks mounted through token and worker changes", () => {
    route.pathname = "/ui/login/";
    let client!: ReturnType<typeof useQueryClient>;
    function Login() {
      client = useQueryClient();
      return null;
    }
    const { rerender } = render(
      <ReactQueryProvider>
        <Login />
      </ReactQueryProvider>,
    );
    const previous = client;
    auth.token = "session-b";
    registerBaseUrlGetter(() => "https://worker.test");
    act(() => resetDashboardSession());
    rerender(
      <ReactQueryProvider>
        <Login />
      </ReactQueryProvider>,
    );
    expect(client).toBe(previous);
    route.pathname = "/ui/api-keys";
    rerender(
      <ReactQueryProvider>
        <Login />
      </ReactQueryProvider>,
    );
    expect(client).not.toBe(previous);
  });
});
