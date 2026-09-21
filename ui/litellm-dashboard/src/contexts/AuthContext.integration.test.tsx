import { StrictMode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearTokenCookies, storeLoginToken } from "@/utils/cookieUtils";
import { AuthProvider, useAuth } from "./AuthContext";
import ReactQueryProvider from "./ReactQueryProvider";

const route = { pathname: "/ui/login/" };
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("@/components/networking", () => ({ getUiConfig: async () => ({}), setGlobalLitellmHeaderName: vi.fn() }));

const loginToken = `e30.${btoa(JSON.stringify({ key: "test-key", user_id: "test-user", user_role: "proxy_admin" }))}.test`;

describe("authentication and query lifecycle", () => {
  beforeEach(() => {
    clearTokenCookies();
    route.pathname = "/ui/login/";
  });
  afterEach(() => {
    act(() => clearTokenCookies());
  });

  it("finishes the login callback after storing a token, then clears derived identity on logout", async () => {
    const completed = vi.fn();
    function Login() {
      const auth = useAuth();
      const login = useMutation({
        mutationFn: async () => {
          storeLoginToken(loginToken);
          return true;
        },
      });
      return (
        <>
          <button onClick={() => login.mutate(undefined, { onSuccess: completed })}>Sign in</button>
          <output>{auth.userID ?? "anonymous"}</output>
        </>
      );
    }
    render(
      <AuthProvider>
        <ReactQueryProvider>
          <Login />
        </ReactQueryProvider>
      </AuthProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("test-user")).toBeInTheDocument();
    expect(completed).toHaveBeenCalledOnce();
    act(() => clearTokenCookies());
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
  });

  it("loads dashboard data under Strict Mode after authentication resolves", async () => {
    storeLoginToken(loginToken);
    route.pathname = "/ui/api-keys";
    const fetchData = vi.fn().mockResolvedValue("loaded records");
    function Dashboard() {
      const query = useQuery({ queryKey: ["records"], queryFn: fetchData });
      return <p>{query.data ?? "loading"}</p>;
    }
    render(
      <StrictMode>
        <AuthProvider>
          <ReactQueryProvider>
            <Dashboard />
          </ReactQueryProvider>
        </AuthProvider>
      </StrictMode>,
    );
    expect(await screen.findByText("loaded records")).toBeInTheDocument();
  });
});
