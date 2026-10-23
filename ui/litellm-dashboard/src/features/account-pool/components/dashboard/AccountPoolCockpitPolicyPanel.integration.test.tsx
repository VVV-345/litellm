import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { CockpitPolicyView } from "../../api/AccountPoolManagementApi";
import { AccountPoolCockpitPolicyPanel } from "./AccountPoolCockpitPolicyPanel";

const initialPolicy: CockpitPolicyView = {
  revision: '"sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
  routing_strategy: "auto",
  custom_rules: [{ account_id: "account-a", priority: 0, weight: 1, is_backup: false, is_preferred: false }],
  account_ids: ["account-a"],
  editable: true,
  blocked_reason: null,
  scope: "codex_pool_global",
  runtime_activation: "restart_required_or_unverified",
  supported_strategies: [
    "auto",
    "random",
    "quota_high_first",
    "quota_low_first",
    "plan_high_first",
    "plan_low_first",
    "expiry_soon_first",
    "custom",
  ],
  unsupported_fields: [
    "account_enabled",
    "candidate_scope",
    "fallback_enabled",
    "max_attempts",
    "cooldown",
    "session_affinity",
  ],
};

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
});
afterEach(() => vi.unstubAllGlobals());

function mountPanel(respond: (init?: RequestInit) => Response | Promise<Response>) {
  const requests: { method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const path = new URL(String(input), "http://localhost").pathname;
      if (path !== "/account_pool/cockpit/policy") throw new Error(`Unexpected request: ${path}`);
      requests.push({ method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      return respond(init);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccountPoolCockpitPolicyPanel accessToken="admin-token" />
    </QueryClientProvider>,
  );
  return { requests, client };
}

it("sends edited global fields and the original revision only on explicit save, without claiming activation", async () => {
  const { requests, client } = mountPanel((init) =>
    Response.json(
      init?.method === "PUT"
        ? {
            ...initialPolicy,
            ...JSON.parse(String(init.body)),
            revision: '"sha256-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
          }
        : initialPolicy,
    ),
  );
  const user = userEvent.setup();
  const strategy = await screen.findByRole("combobox", { name: "选择策略" });
  expect(screen.getByText(/整个 Codex 号池/)).toBeInTheDocument();
  expect(screen.getByText("Cockpit Codex 号池全局策略")).toBeInTheDocument();
  expect(screen.getByText(/不切换现有 LiteLLM 流量/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  await user.click(strategy);
  await user.click(screen.getByRole("option", { name: "自定义优先级与权重" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: "account-a 优先级" }), { target: { value: "9" } });
  fireEvent.change(screen.getByRole("spinbutton", { name: "account-a 权重" }), { target: { value: "3" } });
  await user.click(screen.getByRole("checkbox", { name: "account-a 优先使用" }));
  expect(requests).toEqual([{ method: "GET", body: null }]);
  await user.click(screen.getByRole("button", { name: "保存全局策略" }));
  expect(await screen.findByRole("status")).toHaveTextContent("已保存，待重启或运行状态确认");
  expect(requests).toEqual([
    { method: "GET", body: null },
    {
      method: "PUT",
      body: {
        revision: '"sha256-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        routing_strategy: "custom",
        custom_rules: [{ account_id: "account-a", priority: 9, weight: 3, is_backup: false, is_preferred: true }],
      },
    },
  ]);
  expect(screen.queryByText(/已生效/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  client.clear();
});

it.each([409, 503])(
  "requires a successful re-read after save status %s and never retries the write",
  async (status) => {
    let reads = 0;
    const { requests, client } = mountPanel((init) => {
      if (init?.method === "PUT")
        return Response.json(
          {
            detail: status === 409 ? "revision_conflict" : "write_outcome_unknown",
          },
          { status },
        );
      reads += 1;
      if (reads === 2) return Response.json({ detail: "read unavailable" }, { status: 503 });
      return Response.json({
        ...initialPolicy,
        revision:
          reads === 1
            ? initialPolicy.revision
            : '"sha256-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"',
      });
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "account-a 优先使用" }));
    await user.click(screen.getByRole("button", { name: "保存全局策略" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("必须重新读取");
    expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
    expect(requests.map(({ method }) => method)).toEqual(["GET", "PUT"]);
    await user.click(screen.getByRole("button", { name: "重新读取策略" }));
    expect(await screen.findByText(/read unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重新读取策略" }));
    await waitFor(() => expect(screen.queryByText(/必须重新读取/)).not.toBeInTheDocument());
    await user.click(screen.getByRole("checkbox", { name: "account-a 优先使用" }));
    await user.click(screen.getByRole("button", { name: "保存全局策略" }));
    await screen.findByRole("alert");
    expect(requests.map(({ method }) => method)).toEqual(["GET", "PUT", "GET", "GET", "PUT"]);
    expect(requests[4].body).toMatchObject({
      revision: '"sha256-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"',
    });
    client.clear();
  },
);

it("shows read errors without inventing defaults or allowing writes", async () => {
  const { requests, client } = mountPanel(() => Response.json({ detail: "policy unavailable" }, { status: 503 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("policy unavailable");
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  expect(requests).toEqual([{ method: "GET", body: null }]);
  client.clear();
});

it("preserves unsupported strategy visibility and refuses edits when the server disables the policy", async () => {
  const blockedPolicy = {
    ...initialPolicy,
    routing_strategy: "unknown-strategy",
    editable: false,
    blocked_reason: "unsupported_strategy",
  };
  const { requests, client } = mountPanel(() => Response.json(blockedPolicy));
  expect(await screen.findByText("当前保存的选择策略不受支持，已禁用编辑")).toBeInTheDocument();
  expect(screen.getByText("unknown-strategy")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "选择策略" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "account-a 优先级" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  expect(requests).toEqual([{ method: "GET", body: null }]);
  client.clear();
});

it("locks edits while saving and makes preferred and backup flags mutually exclusive", async () => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const { requests, client } = mountPanel(async (init) => {
    if (init?.method !== "PUT") return Response.json(initialPolicy);
    await pending;
    return Response.json({
      ...initialPolicy,
      ...JSON.parse(String(init.body)),
      revision: '"sha256-dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"',
    });
  });
  const user = userEvent.setup();
  const preferred = await screen.findByRole("checkbox", { name: "account-a 优先使用" });
  await user.click(preferred);
  await user.click(screen.getByRole("checkbox", { name: "account-a 备用账号" }));
  expect(preferred).not.toBeChecked();
  await user.click(screen.getByRole("button", { name: "保存全局策略" }));
  expect(screen.getByRole("button", { name: "正在保存策略" })).toBeDisabled();
  expect(preferred).toHaveAttribute("aria-disabled", "true");
  await act(async () => {
    finish();
  });
  expect(await screen.findByRole("status")).toHaveTextContent("待重启或运行状态确认");
  expect(requests[1].body).toMatchObject({
    custom_rules: [{ account_id: "account-a", priority: 0, weight: 1, is_backup: true, is_preferred: false }],
  });
  client.clear();
});

it("recovers from the initial read failure through an explicit re-read", async () => {
  let reads = 0;
  const { requests, client } = mountPanel(() => {
    reads += 1;
    return reads === 1
      ? Response.json({ detail: "temporarily unavailable" }, { status: 503 })
      : Response.json(initialPolicy);
  });
  const user = userEvent.setup();
  expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
  await user.click(screen.getByRole("button", { name: "重新读取策略" }));
  expect(await screen.findByRole("spinbutton", { name: "account-a 优先级" })).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "account-a 优先使用" }));
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeEnabled();
  expect(requests).toEqual([
    { method: "GET", body: null },
    { method: "GET", body: null },
  ]);
  client.clear();
});

it.each([null, {}])("rejects an incomplete 200 save receipt %j and requires a re-read", async (receipt) => {
  const { requests, client } = mountPanel((init) => {
    if (init?.method !== "PUT") return Response.json(initialPolicy);
    return receipt === null ? new Response(null, { status: 200 }) : Response.json(receipt);
  });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("checkbox", { name: "account-a 优先使用" }));
  await user.click(screen.getByRole("button", { name: "保存全局策略" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("必须重新读取");
  expect(screen.queryByText("已保存，待重启或运行状态确认")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  expect(requests.map(({ method }) => method)).toEqual(["GET", "PUT"]);
  client.clear();
});

it("rejects an invalid read revision instead of enabling writes", async () => {
  const { client } = mountPanel(() => Response.json({ ...initialPolicy, revision: "invalid-revision" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("响应无效");
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存全局策略" })).toBeDisabled();
  client.clear();
});
