import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { DATA_CHANGED_EVENT } from "@/lib/cacheEvents";
import { listAccountPoolEnvironments } from "@/features/account-pool/api/AccountPoolApi";
import { ApiError } from "@/lib/http/client";
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

it("discovers Codex models without saving or changing the draft, then enables all only on save", async () => {
  const card = {
    ...environment,
    channel: "cliproxyapi",
    supplier: "openai_codex",
    authorization_flow: "browser_oauth",
    proxy_mode: "default_gateway",
    proxy_profile_id: null,
    available_models: ["model-a", "model-removed"],
    enabled_models: ["model-a"],
    configured_models: ["model-a"],
  } satisfies AccountPoolEnvironment;
  const saveRequests: string[][] = [];
  const catalogs = [
    Response.json(["model-a", "model-b", "model-removed"]),
    Response.json(["model-a", "model-b", "model-new"]),
    Response.json({ detail: "unavailable" }, { status: 503 }),
  ];
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
    if (String(input).endsWith("/model-catalog")) return catalogs.shift()!;
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      saveRequests.push(body.enabled_models);
      return Response.json({ ...card, ...body });
    }
    return Response.json([]);
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const unrelated = { ...card, id: "other-card" };
  client.setQueryData(accountPoolQueryKeys.environments("token"), [card, unrelated]);
  client.setQueryData(accountPoolQueryKeys.environments("other-user"), [card]);
  const dispatchEvent = vi.spyOn(window, "dispatchEvent");
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <RuntimeConfigDialog
        accessToken="token"
        environment={card}
        open
        onOpenChange={vi.fn()}
        onRefresh={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const restored = await screen.findByRole("checkbox", { name: "model-b" });
  expect(restored).not.toBeChecked();
  expect(dispatchEvent.mock.calls.some(([event]) => event.type === DATA_CHANGED_EVENT)).toBe(false);
  expect(client.getQueryData(accountPoolQueryKeys.environments("token"))).toEqual([
    { ...card, available_models: ["model-a", "model-b", "model-removed"] },
    unrelated,
  ]);
  expect(client.getQueryData(accountPoolQueryKeys.environments("other-user"))).toEqual([card]);
  fireEvent.click(restored);
  expect(restored).toBeChecked();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "unsaved name" } });
  fireEvent.click(screen.getByRole("button", { name: "重新发现模型" }));
  expect(await screen.findByRole("checkbox", { name: "model-new" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "model-a" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "model-b" })).toBeChecked();
  expect(screen.queryByRole("checkbox", { name: "model-removed" })).not.toBeInTheDocument();
  expect(screen.getByText(/新增：model-new.*移除：model-removed/)).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("unsaved name");
  fireEvent.click(screen.getByRole("button", { name: "重新发现模型" }));
  await screen.findByText(/未能发现模型/);
  expect(screen.getByRole("checkbox", { name: "model-new" })).not.toBeChecked();
  expect(saveRequests).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "启用全部" }));
  expect(screen.getByRole("checkbox", { name: "model-b" })).toBeChecked();
  expect(saveRequests).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await waitFor(() => expect(saveRequests).toEqual([["model-a", "model-b", "model-new"]]));
  unmount();
  client.clear();
});

it.each([401, 403])(
  "does not clear an HTTP %s environment denial when a model catalog request finishes later",
  async (status) => {
    const card = {
      ...environment,
      channel: "cliproxyapi",
      supplier: "openai_codex",
      proxy_mode: "default_gateway",
      proxy_profile_id: null,
      available_models: ["model-a"],
      enabled_models: ["model-a"],
    } satisfies AccountPoolEnvironment;
    const pending = Promise.withResolvers<Response>();
    const environments = [Response.json({ detail: "permission denied" }, { status }), Response.json([card])];
    const fetch = vi.fn<typeof globalThis.fetch>(async (input) => {
      if (String(input).endsWith("/model-catalog")) return pending.promise;
      if (String(input).endsWith("/environments")) return environments.shift()!;
      return Response.json([]);
    });
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const environmentsKey = accountPoolQueryKeys.environments("token");
    client.setQueryData(environmentsKey, [card]);
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <RuntimeConfigDialog
          accessToken="token"
          environment={card}
          open
          onOpenChange={vi.fn()}
          onRefresh={vi.fn()}
          onSaved={vi.fn()}
        />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(fetch.mock.calls.some(([input]) => String(input).endsWith("/model-catalog"))).toBe(true),
    );
    await expect(
      client.fetchQuery({
        queryKey: environmentsKey,
        queryFn: () => listAccountPoolEnvironments("token"),
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(client.getQueryState(environmentsKey)?.status).toBe("error");
    await act(async () => pending.resolve(Response.json(["model-a", "model-b"])));
    await screen.findByRole("checkbox", { name: "model-b" });
    expect(client.getQueryState(environmentsKey)?.error).toMatchObject({ status });
    expect(client.getQueryState(environmentsKey)?.status).toBe("error");
    expect(client.getQueryData(environmentsKey)).toEqual([card]);
    await expect(
      client.fetchQuery({
        queryKey: environmentsKey,
        queryFn: () => listAccountPoolEnvironments("token"),
      }),
    ).resolves.toEqual([card]);
    expect(client.getQueryState(environmentsKey)?.status).toBe("success");
    expect(client.getQueryState(environmentsKey)?.error).toBeNull();
    unmount();
    client.clear();
  },
);
