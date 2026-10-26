import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { AccountPoolBackendBoundary } from "./AccountPoolNativeCockpitPanel";

const subject = "a".repeat(64);
const revision = '"sha256-' + "b".repeat(64) + '"';
const prefix = "/account_pool/cockpit/native";
beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
});
afterEach(() => vi.unstubAllGlobals());

function mount(conflict = false) {
  let state = "absent";
  const calls: { path: string; method: string; body: unknown }[] = [];
  const view = () => ({
    scope: {
      subject_hash: subject,
      state,
      account_ids: [],
      models: ["fixture-model"],
      model_scope: "restricted",
      persisted_manifest_revision: revision,
      runtime_activation: "restart_required_or_unverified",
      ready_to_migrate: false,
    },
    runtime: {
      persisted_revision: revision,
      active_revision: '"sha256-' + "c".repeat(64) + '"',
      apply_supported: false,
      blocked_reason: "non_policy_change",
    },
    runtime_state: "pending",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ path, method, body });
      if (!path.startsWith(prefix)) throw new Error("Manager API must not be called");
      if (path.endsWith("/configuration")) return Response.json({ backend: "cockpit" });
      if (path.endsWith("/accounts"))
        return Response.json([{ account_id: "00000000-0000-0000-0000-000000000001", credential_present: true }]);
      if (path.endsWith("/policy")) {
        const policy = {
          revision,
          routing_strategy: "auto",
          custom_rules: [],
          account_ids: [],
          editable: true,
          blocked_reason: null,
          scope: "codex_pool_global",
          runtime_activation: "restart_required_or_unverified",
          supported_strategies: ["auto"],
          unsupported_fields: [],
        };
        return Response.json(policy);
      }
      if (path.endsWith("/preflight")) {
        const preflight = {
          key_hash: subject,
          status: "eligible",
          code: null,
          revision,
          models: ["fixture-model"],
        };
        return Response.json(preflight);
      }
      if (path.includes("/credential-receipts")) {
        const receipt = {
          key_hash: subject,
          status: "native_scope_verified",
          receipt: "SIGNED-FIXTURE",
          code: null,
          ready_to_migrate: false,
        };
        return Response.json(receipt);
      }
      if (method === "POST" && path.endsWith("/scope")) {
        if (conflict) return Response.json({ detail: "revision_conflict" }, { status: 409 });
        state = "staged";
      }
      if (path.endsWith("/activate")) state = "active";
      if (path.endsWith("/deactivate")) state = "staged";
      if (method === "DELETE") state = "absent";
      return Response.json(view());
    }),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AccountPoolBackendBoundary accessToken="admin-token">
        <p>旧 Manager 页面</p>
      </AccountPoolBackendBoundary>
    </QueryClientProvider>,
  );
  return calls;
}

async function prepare() {
  const user = userEvent.setup();
  fireEvent.change(await screen.findByLabelText("原生 Key 哈希"), { target: { value: subject } });
  await user.click(screen.getByRole("button", { name: "选择主体" }));
  await user.click(await screen.findByRole("button", { name: "预检权限与凭据" }));
  await screen.findByText("预检通过");
  await user.click(screen.getByRole("button", { name: "签发回执" }));
  await screen.findByText("回执已签发，尚未重验");
  await user.click(screen.getByRole("button", { name: "重新验证回执" }));
  await screen.findByText("回执重验通过");
  return user;
}

it("runs the native workflow without mounting Manager UI or claiming an unconfirmed revision is active", async () => {
  const calls = mount();
  const user = await prepare();
  expect(screen.queryByText("旧 Manager 页面")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "发布停用投影" }));
  await screen.findByText("投影：已发布，停用");
  expect(calls.find((call) => call.method === "POST" && call.path.endsWith("/scope"))?.body).toEqual({
    subject_hash: subject,
    receipt: "SIGNED-FIXTURE",
    revision,
  });
  await user.click(screen.getByRole("button", { name: "启用投影" }));
  await screen.findByText("投影：已启用");
  expect(screen.getByText("运行态：待确认，不能视为已生效")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "停用投影" }));
  await screen.findByText("投影：已发布，停用");
  await user.click(screen.getByRole("button", { name: "撤销投影" }));
  await screen.findByText("投影：未发布");
  expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  expect(screen.queryByText("SIGNED-FIXTURE")).not.toBeInTheDocument();
});

it("does not retry conflict writes and requires a fresh receipt after failure", async () => {
  const calls = mount(true);
  const user = await prepare();
  await user.click(screen.getByRole("button", { name: "发布停用投影" }));
  await screen.findByRole("alert");
  expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/scope"))).toHaveLength(1);
  expect(screen.getByRole("button", { name: "发布停用投影" })).toBeDisabled();
});

it("never falls back to Manager when backend configuration cannot be read", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => Response.json({ detail: "unavailable" }, { status: 503 })),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountPoolBackendBoundary accessToken="admin-token">
        <p>旧 Manager 页面</p>
      </AccountPoolBackendBoundary>
    </QueryClientProvider>,
  );
  await screen.findByRole("alert");
  expect(screen.queryByText("旧 Manager 页面")).not.toBeInTheDocument();
});
