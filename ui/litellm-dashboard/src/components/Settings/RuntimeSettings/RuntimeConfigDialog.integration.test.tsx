import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { RuntimeConfigDialog } from "./RuntimeConfigDialog";
import type { AccountPoolEnvironment } from "@/features/account-pool/utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "@/features/account-pool/hooks/accountPoolQueryKeys";

const environment = {
  id: "card",
  version: 1,
  name: "original",
  status: "ready",
  configuration_pending: false,
  concurrency_limit: 2,
  enabled: true,
  manual_cooldown: false,
  proxy_mode: "profile",
  proxy_profile_id: "proxy",
  available_models: [],
  enabled_models: [],
  quota: { windows: [] },
  model_quotas: [],
} as unknown as AccountPoolEnvironment;

beforeEach(() => {
  void i18n.changeLanguage("zh-CN");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("keeps cached proxy choices editable and preserves a draft when a background refresh fails", async () => {
  const pending = Promise.withResolvers<Response>();
  const fetch = vi.fn<typeof globalThis.fetch>(async (input) =>
    String(input).includes("proxy-profiles") ? pending.promise : Response.json([]),
  );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(accountPoolQueryKeys.proxyProfiles("token"), [{ id: "proxy", name: "cached proxy" }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const close = vi.fn();
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <RuntimeConfigDialog
        accessToken="token"
        environment={environment}
        open
        onOpenChange={close}
        onRefresh={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  expect(screen.getAllByRole("combobox")[1]).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "unsaved name" } });
  await act(async () => {
    pending.resolve(Response.json({ error: "offline" }, { status: 503 }));
  });
  await screen.findByRole("alert");
  expect(screen.getByRole("textbox")).toHaveValue("unsaved name");
  expect(screen.getAllByRole("combobox")[1]).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(close).not.toHaveBeenCalled();
  unmount();
  client.clear();
});

it("clears the unsaved warning after a successful configuration save", async () => {
  const saved = {
    ...environment,
    version: 2,
    name: "saved name",
    proxy_mode: "default_gateway",
    proxy_profile_id: null,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof globalThis.fetch>(async (_input, init) =>
      init?.method === "PUT" ? Response.json(saved) : Response.json([]),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const onSaved = vi.fn();
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <RuntimeConfigDialog
        accessToken="token"
        environment={{ ...environment, proxy_mode: "default_gateway", proxy_profile_id: null }}
        open
        onOpenChange={vi.fn()}
        onRefresh={vi.fn()}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  const save = screen.getByRole("button", { name: "保存配置" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "saved name" } });
  fireEvent.click(save);
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(confirm).not.toHaveBeenCalled();
  unmount();
  client.clear();
});
