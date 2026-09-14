import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http/client";
import { toast } from "@/lib/toast";

import {
  authorizeAccountPoolEnvironment,
  deleteAccountPoolEnvironment,
  listAccountPoolEnvironments,
  updateAccountPoolEnvironment,
} from "./AccountPoolApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { useAccountPoolMutations } from "./useAccountPoolMutations";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("./AccountPoolApi", () => ({
  authorizeAccountPoolEnvironment: vi.fn(),
  deleteAccountPoolEnvironment: vi.fn(),
  listAccountPoolEnvironments: vi.fn(),
  updateAccountPoolEnvironment: vi.fn(),
}));

const deleteEnvironmentMock = vi.mocked(deleteAccountPoolEnvironment);
const listEnvironmentsMock = vi.mocked(listAccountPoolEnvironments);
const environment = {
  id: "environment-1",
  status: "ready",
  configuration_pending: false,
} as AccountPoolEnvironment;

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const TestQueryProvider = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return TestQueryProvider;
};

describe("useAccountPoolMutations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(authorizeAccountPoolEnvironment).mockReset();
    vi.mocked(updateAccountPoolEnvironment).mockReset();
  });

  it("treats a gateway error as success when the environment is already deleted", async () => {
    const gatewayError = new ApiError("<html>Bad Gateway</html>", 502, "<html>Bad Gateway</html>");
    const onDeleted = vi.fn();
    deleteEnvironmentMock.mockRejectedValueOnce(gatewayError);
    listEnvironmentsMock.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useAccountPoolMutations("token", true, vi.fn(), onDeleted), {
      wrapper: createWrapper(),
    });
    result.current.deleteMutation.mutate(environment);

    await waitFor(() => expect(result.current.deleteMutation.isSuccess).toBe(true));
    expect(listEnvironmentsMock).toHaveBeenCalledWith("token");
    expect(onDeleted).toHaveBeenCalledOnce();
    expect(toast.success).toHaveBeenCalledWith("accountPool.mutation.deleteRequested");
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.fromError).not.toHaveBeenCalled();
  });

  it("shows a short error when the environment still exists after a gateway error", async () => {
    const gatewayError = new ApiError("<html>Bad Gateway</html>", 502, "<html>Bad Gateway</html>");
    const onDeleted = vi.fn();
    deleteEnvironmentMock.mockRejectedValueOnce(gatewayError);
    listEnvironmentsMock.mockResolvedValueOnce([environment]);

    const { result } = renderHook(() => useAccountPoolMutations("token", true, vi.fn(), onDeleted), {
      wrapper: createWrapper(),
    });
    result.current.deleteMutation.mutate(environment);

    await waitFor(() => expect(result.current.deleteMutation.isError).toBe(true));
    expect(onDeleted).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("accountPool.mutation.deleteFailed");
    expect(toast.fromError).not.toHaveBeenCalled();
  });
});
