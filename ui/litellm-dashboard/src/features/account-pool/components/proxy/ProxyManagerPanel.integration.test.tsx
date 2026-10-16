/** 本文件验证代理面板的刷新、节点切换与延迟状态展示，仅替换网络请求。 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { ProxyManagerPanel } from "./ProxyManagerPanel";
import type {
  AccountPoolEnvironment,
  AccountPoolISPProxy,
  AccountPoolProxyConfigurationProfile,
  AccountPoolProxyGateway,
  AccountPoolProxyGatewayDelay,
} from "../../utils/AccountPoolTypes";

const gateways: AccountPoolProxyGateway[] = [
  {
    port: 7891,
    profile_id: "clash-gateway-7891",
    name: "Clash 端口 7891",
    proxy_url: "http://host.docker.internal:7891",
    current_node: "美国01",
  },
];

const getConfiguration = vi.fn().mockResolvedValue({ config_path: "/opt/litellm/mihomo/config.yaml" });
const listGateways = vi.fn();
const listNodes = vi.fn();
const switchGateway = vi.fn();
const measureDelays = vi.fn();
const updateConfiguration = vi.fn();
const listConfigurations = vi.fn();
const saveConfiguration = vi.fn();
const activateConfiguration = vi.fn();
const deleteConfiguration = vi.fn();
const listIsps = vi.fn();
const importIsps = vi.fn();
const updateIsp = vi.fn();
const deleteIsp = vi.fn();
const bindIsp = vi.fn();
const probeIsp = vi.fn();
const ispProfile: AccountPoolISPProxy = {
  id: "isp-1", name: "ISP A", protocol: "http", host: "proxy.test", port: 8080,
  expires_on: null, enabled: true, credentials_configured: true, bound_ports: [], last_probe: null,
};
const delay: AccountPoolProxyGatewayDelay = {
  port: 7891,
  current_node: "美国01",
  status: "ok",
  delay_ms: 183,
  checked_at: "2026-09-08T12:00:00Z",
  gateway_status: "ok",
  gateway_delay_ms: 196,
};

vi.mock("../../api/AccountPoolApi", () => ({
  getAccountPoolProxyGatewayConfiguration: (...args: unknown[]) => getConfiguration(...args),
  listAccountPoolProxyGateways: (...args: unknown[]) => listGateways(...args),
  listAccountPoolClashNodes: (...args: unknown[]) => listNodes(...args),
  switchAccountPoolProxyGateway: (...args: unknown[]) => switchGateway(...args),
  measureAccountPoolProxyGatewayDelays: (...args: unknown[]) => measureDelays(...args),
  updateAccountPoolProxyGatewayConfiguration: (...args: unknown[]) => updateConfiguration(...args),
  listAccountPoolProxyConfigurations: (...args: unknown[]) => listConfigurations(...args),
  saveAccountPoolProxyConfiguration: (...args: unknown[]) => saveConfiguration(...args),
  activateAccountPoolProxyConfiguration: (...args: unknown[]) => activateConfiguration(...args),
  deleteAccountPoolProxyConfiguration: (...args: unknown[]) => deleteConfiguration(...args),
  listAccountPoolISPProxies: (...args: unknown[]) => listIsps(...args),
  importAccountPoolISPProxies: (...args: unknown[]) => importIsps(...args),
  updateAccountPoolISPProxy: (...args: unknown[]) => updateIsp(...args),
  deleteAccountPoolISPProxy: (...args: unknown[]) => deleteIsp(...args),
  probeAccountPoolISP: (...args: unknown[]) => probeIsp(...args),
  bindAccountPoolProxyGatewayISP: (...args: unknown[]) => bindIsp(...args),
}));

const renderPanel = (enabled = true, environments: AccountPoolEnvironment[] = []) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProxyManagerPanel
        accessToken="token"
        enabled={enabled}
        environments={environments}
        environmentsLoading={false}
      />
    </QueryClientProvider>,
  );
};

describe("ProxyManagerPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
    vi.resetAllMocks();
    getConfiguration.mockResolvedValue({ config_path: "/opt/litellm/mihomo/config.yaml" });
    listGateways.mockResolvedValue(gateways);
    listNodes.mockResolvedValue([{ name: "美国01", proxy_type: "Shadowsocks" }]);
    measureDelays.mockResolvedValue([delay]);
    updateConfiguration.mockResolvedValue({ config_path: "/opt/litellm/mihomo/config.yaml" });
    listConfigurations.mockResolvedValue([]);
    saveConfiguration.mockImplementation(async (_token: string, request: { name: string }) => ({
      id: request.name,
      name: request.name,
      created_at: "2026-10-14T12:00:00Z",
      active: false,
    }));
    activateConfiguration.mockResolvedValue({ id: "home.yaml", name: "home.yaml", active: true });
    deleteConfiguration.mockResolvedValue(undefined);
    listIsps.mockResolvedValue([]);
    importIsps.mockResolvedValue([]);
    updateIsp.mockResolvedValue(undefined);
    deleteIsp.mockResolvedValue(undefined);
    bindIsp.mockResolvedValue(undefined);
  });

  it("keeps ISP and configuration visible with port settings collapsed until opened", async () => {
    const user = userEvent.setup();
    renderPanel();
    const configuration = await screen.findByLabelText("配置名称");
    const isp = screen.getByText("ISP 代理");
    expect(configuration).toBeVisible();
    expect(isp).toBeVisible();
    expect(isp.compareDocumentPosition(configuration) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const trigger = screen.getByRole("button", { name: /Clash 端口设置/ });
    expect(configuration.compareDocumentPosition(trigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("combobox", { name: /Clash 端口 7891/ })).not.toBeInTheDocument();
    await user.click(trigger);
    expect(await screen.findByRole("combobox", { name: /Clash 端口 7891/ })).toBeVisible();
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("switches nodes without restarting the all-gateway delay check", async () => {
    const user = userEvent.setup();
    listGateways.mockResolvedValue(gateways);
    listNodes.mockResolvedValue([
      { name: "美国01", proxy_type: "Shadowsocks" },
      { name: "日本02", proxy_type: "Vmess" },
    ]);
    switchGateway.mockImplementation(async () => {
      listGateways.mockResolvedValue([{ ...gateways[0], current_node: "日本02" }]);
      return { ...gateways[0], current_node: "日本02" };
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    expect(await screen.findByText("当前节点：美国01")).toBeInTheDocument();
    expect(await screen.findByText("当前配置文件：/opt/litellm/mihomo/config.yaml")).toBeInTheDocument();
    expect(await screen.findByText("节点延迟：183 ms")).toBeInTheDocument();
    expect(measureDelays).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("combobox", { name: /Clash 端口 7891/ }));
    await user.click(await screen.findByRole("option", { name: "日本02" }));

    expect(switchGateway).toHaveBeenCalledWith("token", 7891, "日本02");
    expect(await screen.findByText("当前节点：日本02")).toBeInTheDocument();
    expect(screen.getByText("节点延迟：未检测")).toBeInTheDocument();
    expect(screen.queryByText("节点延迟：183 ms")).not.toBeInTheDocument();
    expect(measureDelays).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("combobox", { name: /Clash 端口 7891/ })).toBeEnabled();
  });

  it("reports a rejected switch and retains the observed current node", async () => {
    const user = userEvent.setup();
    listGateways.mockResolvedValue(gateways);
    listNodes.mockResolvedValue([{ name: "日本02", proxy_type: "Vmess" }]);
    switchGateway.mockRejectedValue(new Error("clash rejected the selection"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    await user.click(await screen.findByRole("combobox", { name: /Clash 端口 7891/ }));
    await user.click(await screen.findByRole("option", { name: "日本02" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/切换失败|Unable to switch/i);
    expect(screen.getByText("当前节点：美国01")).toBeInTheDocument();
  });

  it("shows the Clash error hint when the manager cannot reach Clash", async () => {
    listGateways.mockRejectedValue(new Error("clash controller returned status 502"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Clash/);
  });

  it("renders nothing when disabled", () => {
    listGateways.mockResolvedValue([]);
    listNodes.mockResolvedValue([]);
    renderPanel(false);

    expect(screen.queryByTestId("proxy-manager-panel")).not.toBeInTheDocument();
    expect(measureDelays).not.toHaveBeenCalled();
  });

  it("refreshes latency and hides the old value while checking", async () => {
    const user = userEvent.setup();
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));
    expect(await screen.findByText("节点延迟：183 ms")).toBeInTheDocument();
    const pending = Promise.withResolvers<AccountPoolProxyGatewayDelay[]>();
    measureDelays.mockReturnValueOnce(pending.promise);

    await user.click(screen.getByRole("button", { name: "刷新号池" }));

    expect(await screen.findByText("节点延迟：检测中…")).toBeInTheDocument();
    expect(screen.queryByText("节点延迟：183 ms")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新号池" })).toBeDisabled();
    await act(async () => pending.resolve([{ ...delay, status: "timeout", delay_ms: null }]));
    expect(await screen.findByText("节点延迟：超时")).toBeInTheDocument();
    expect(measureDelays).toHaveBeenCalledTimes(2);
    expect(measureDelays).toHaveBeenLastCalledWith("token");
  });

  it("reports a failed refresh instead of retaining a successful measurement", async () => {
    const user = userEvent.setup();
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));
    expect(await screen.findByText("节点延迟：183 ms")).toBeInTheDocument();
    measureDelays.mockRejectedValueOnce(new Error("controller unavailable"));

    await user.click(screen.getByRole("button", { name: "刷新号池" }));

    expect(await screen.findByText("节点延迟：检测失败")).toBeInTheDocument();
    expect(screen.queryByText("节点延迟：183 ms")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新号池" })).toBeEnabled());
  });

  it("does not attach an old node measurement to a new node", async () => {
    listGateways.mockResolvedValue([{ ...gateways[0], current_node: "日本02" }]);
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    expect(await screen.findByText("当前节点：日本02")).toBeInTheDocument();
    expect(await screen.findByText("节点延迟：未检测")).toBeInTheDocument();
    expect(screen.queryByText("节点延迟：183 ms")).not.toBeInTheDocument();
  });

  it("updates the proxy configuration from a subscription URL", async () => {
    const user = userEvent.setup();
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    await user.type(await screen.findByLabelText("订阅链接"), "https://example.test/config.yaml");
    await user.click(screen.getByRole("button", { name: "更新代理配置" }));

    expect(updateConfiguration).toHaveBeenCalledWith("token", {
      content: undefined,
      url: "https://example.test/config.yaml",
    });
    expect(await screen.findByRole("status")).toHaveTextContent("代理配置已更新");
  });

  it("saves multiple configuration files without replacing the active configuration", async () => {
    const user = userEvent.setup();
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));
    const fileInput = await screen.findByLabelText("配置文件");
    const files = [
      new File(["proxies: []\n"], "office.yaml", { type: "text/yaml" }),
      new File(["proxies: []\n"], "home.yaml", { type: "text/yaml" }),
    ];
    Object.defineProperty(files[0], "text", { value: async () => "proxies: []\n" });
    Object.defineProperty(files[1], "text", { value: async () => "proxies: []\n" });

    await user.upload(fileInput, files);
    await user.click(screen.getByRole("button", { name: "保存配置文件" }));

    expect(saveConfiguration).toHaveBeenNthCalledWith(1, "token", { name: "office.yaml", content: "proxies: []\n" });
    expect(saveConfiguration).toHaveBeenNthCalledWith(2, "token", { name: "home.yaml", content: "proxies: []\n" });
    expect(activateConfiguration).not.toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("配置文件已保存");
  });

  it("switches to a saved configuration profile", async () => {
    const user = userEvent.setup();
    const profiles: AccountPoolProxyConfigurationProfile[] = [
      { id: "office", name: "office.yaml", created_at: "2026-10-13T12:00:00Z", active: true },
      { id: "home", name: "home.yaml", created_at: "2026-10-14T12:00:00Z", active: false },
    ];
    listConfigurations.mockResolvedValue(profiles);
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    await user.click(await screen.findByRole("button", { name: "启用代理配置 home.yaml" }));

    expect(activateConfiguration).toHaveBeenCalledWith("token", "home");
  });

  it("binds an ISP to a port and shows its card usage", async () => {
    const user = userEvent.setup();
    const profile: AccountPoolISPProxy = {
      id: "isp-1",
      name: "Residential ISP",
      protocol: "http",
      host: "proxy.example.test",
      port: 1234,
      expires_on: "2026-12-31",
      enabled: true,
      credentials_configured: false,
      bound_ports: [],
      last_probe: null,
    };
    listIsps.mockResolvedValue([profile]);
    const environments = [
      { id: "card-1", proxy_profile_id: "clash-gateway-7891" },
      { id: "card-2", proxy_profile_id: "clash-gateway-7891" },
    ] as AccountPoolEnvironment[];
    renderPanel(true, environments);
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    expect(await screen.findByText("占用：2 张卡片")).toBeInTheDocument();
    await user.click(await screen.findByRole("combobox", { name: /Clash 端口 7891/ }));
    await user.click(await screen.findByRole("option", { name: "Residential ISP" }));

    expect(bindIsp).toHaveBeenCalledWith("token", 7891, "isp-1");
  });

  it("shows the persisted probe latency beside the ISP card title", async () => {
    await i18n.changeLanguage("zh-CN");
    listIsps.mockResolvedValue([{
      id: "isp-1",
      name: "ISP A",
      protocol: "http",
      host: "proxy.test",
      port: 8080,
      expires_on: null,
      enabled: true,
      credentials_configured: true,
      bound_ports: [],
      last_probe: {
        profile_id: "isp-1",
        status: "ok",
        checked_at: new Date().toISOString(),
        exit_ip: "8.8.8.8",
        delay_ms: 82,
      },
    }]);
    renderPanel();

    expect(await screen.findByText("ISP A")).toHaveTextContent("延迟 82 ms");
  });

  it("distinguishes saved credentials from an unauthenticated ISP and sends explicit clearing", async () => {
    listIsps.mockResolvedValue([ispProfile]);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "编辑 ISP A" }));
    expect(screen.getByLabelText("用户名")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveAttribute("placeholder", "已保存，留空保持不变");
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "ISP renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("ISP 配置已保存");
    expect(updateIsp).toHaveBeenCalledWith("token", "isp-1", expect.objectContaining({
      name: "ISP renamed", username: null, password: null, clear_credentials: false,
    }));
    fireEvent.click(screen.getByRole("button", { name: "编辑 ISP A" }));
    fireEvent.click(screen.getByRole("button", { name: "清除凭据" }));
    expect(screen.getByRole("alert")).toHaveTextContent("保存后将清除");
    listIsps.mockResolvedValue([{ ...ispProfile, credentials_configured: false }]);
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(updateIsp).toHaveBeenLastCalledWith("token", "isp-1", expect.objectContaining({
      username: null, password: null, clear_credentials: true,
    })));
    fireEvent.click(await screen.findByRole("button", { name: "编辑 ISP A" }));
    expect(screen.getByLabelText("密码")).toHaveAttribute("placeholder", "密码");
    expect(screen.getByRole("button", { name: "清除凭据" })).toBeDisabled();
  });

  it("keeps the last saved result visible when the probe request or persistence fails", async () => {
    listIsps.mockResolvedValue([{
      ...ispProfile,
      last_probe: { profile_id: "isp-1", status: "ok", checked_at: new Date().toISOString(), delay_ms: 82, exit_ip: "8.8.8.8" },
    }]);
    probeIsp.mockRejectedValue(new Error("database credentials must not be shown"));
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "检测 ISP A" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("探测请求或保存失败");
    expect(screen.getByText("出口 IP：8.8.8.8")).toBeVisible();
    expect(screen.queryByText(/database credentials/)).not.toBeInTheDocument();
  });

  it("uses the latest server result after a refresh instead of an older local probe", async () => {
    const current = { profile_id: "isp-1", status: "ok", checked_at: new Date().toISOString(), delay_ms: 82, exit_ip: "8.8.8.8" };
    listIsps.mockResolvedValue([ispProfile]);
    probeIsp.mockImplementation(async () => {
      listIsps.mockResolvedValue([{ ...ispProfile, last_probe: current }]);
      return current;
    });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "检测 ISP A" }));
    await screen.findByText("出口 IP：8.8.8.8");
    listIsps.mockResolvedValue([{ ...ispProfile, last_probe: { ...current, exit_ip: "1.1.1.1", delay_ms: 16 } }]);
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));
    fireEvent.click(screen.getByRole("button", { name: "刷新号池" }));
    expect(await screen.findByText("出口 IP：1.1.1.1")).toBeVisible();
    expect(screen.queryByText("出口 IP：8.8.8.8")).not.toBeInTheDocument();
  });

  it("marks expired latency and does not hide cached ISP cards on a list failure", async () => {
    listIsps.mockResolvedValue([{
      ...ispProfile,
      last_probe: { profile_id: "isp-1", status: "ok", checked_at: "2020-01-01T00:00:00Z", delay_ms: 82 },
    }]);
    renderPanel();
    expect(await screen.findByText("ISP A")).toHaveTextContent("82 ms，数据已过期");
    listIsps.mockRejectedValue(new Error("database credentials must not be shown"));
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));
    fireEvent.click(screen.getByRole("button", { name: "刷新号池" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ISP 列表读取失败");
    expect(screen.getByText("ISP A")).toBeVisible();
    expect(screen.queryByText("暂无 ISP 配置")).not.toBeInTheDocument();
  });

  it("imports ISP records without clearing the form before the request resolves", async () => {
    const user = userEvent.setup();
    const imported: AccountPoolISPProxy[] = [];
    importIsps.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve(imported), 10)));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /Clash 端口设置/ }));

    const input = await screen.findByLabelText("批量导入 ISP");
    await user.type(input, "http://proxy.example.test:1234");
    await user.click(screen.getByRole("button", { name: "导入 ISP" }));

    expect(importIsps).toHaveBeenCalledWith("token", "http://proxy.example.test:1234");
    expect(await screen.findByRole("status")).toHaveTextContent("ISP 已导入");
  });

  it("detects ISP exit details on demand and replaces stale results after failure", async () => {
    await i18n.changeLanguage("zh-CN");
    const profile: AccountPoolISPProxy = { id: "isp-1", name: "ISP A", protocol: "http", host: "proxy.test",
      port: 8080, expires_on: null, enabled: true, credentials_configured: false, bound_ports: [], last_probe: null };
    listIsps.mockResolvedValue([profile]);
    const savedProbe = { profile_id: profile.id, status: "ok", exit_ip: "8.8.8.8", delay_ms: 42,
      country: "United States", region: "California", city: "Mountain View", isp: "Example operator",
       checked_at: "2026-10-15T00:00:00Z" };
    probeIsp.mockImplementation(async () => {
      listIsps.mockResolvedValue([{ ...profile, last_probe: savedProbe }]);
      return savedProbe;
    });
    renderPanel();
    const button = await screen.findByRole("button", { name: "检测 ISP A" });
    expect(probeIsp).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(await screen.findByText("出口 IP：8.8.8.8")).toBeVisible();
    expect(screen.getByText("请求延迟：42 ms")).toBeVisible();
    expect(screen.getByText(/位置（近似）：United States/)).toHaveTextContent("Mountain View");
    expect(screen.getByText("运营商：Example operator")).toBeVisible();
    expect(probeIsp).toHaveBeenCalledWith("token", "isp-1");
    probeIsp.mockImplementationOnce(async () => {
      const failedProbe = { profile_id: profile.id, status: "error", checked_at: new Date().toISOString(), error: "probe_failed" };
      listIsps.mockResolvedValue([{ ...profile, last_probe: failedProbe }]);
      return failedProbe;
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(await screen.findByText("连通状态：检测失败")).toBeVisible();
    expect(screen.queryByText("出口 IP：8.8.8.8")).not.toBeInTheDocument();
    expect(updateIsp).not.toHaveBeenCalled();
  });

  it("checks all ISP profiles sequentially and continues after a failed request", async () => {
    await i18n.changeLanguage("zh-CN");
    probeIsp.mockReset();
    listIsps.mockResolvedValue(["A", "B"].map((name) => ({ id: name, name, protocol: "http",
      host: "proxy.test", port: 8080, expires_on: null, enabled: true, credentials_configured: false,
      bound_ports: [], last_probe: null })));
    const pending = Promise.withResolvers<unknown>();
    probeIsp.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ profile_id: "B", status: "timeout",
      exit_ip: null, delay_ms: null, checked_at: "2026-10-15T00:00:00Z" });
    renderPanel();
    await screen.findByRole("button", { name: "检测 A" });
    fireEvent.click(screen.getByRole("button", { name: "检测全部 ISP" }));
    await waitFor(() => expect(probeIsp).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "检测全部 ISP" })).toBeDisabled();
    await act(async () => pending.reject(new Error("unavailable")));
    expect(await screen.findByText("连通状态：超时")).toBeVisible();
    expect(screen.getByText("连通状态：未检测")).toBeVisible();
    expect(probeIsp).toHaveBeenNthCalledWith(2, "token", "B");
  });

});
