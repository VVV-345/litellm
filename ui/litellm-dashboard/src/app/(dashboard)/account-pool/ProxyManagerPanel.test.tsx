import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ProxyManagerPanel } from "./ProxyManagerPanel";
import type { AccountPoolProxyGateway } from "./AccountPoolTypes";

const gateways: AccountPoolProxyGateway[] = [
  {
    port: 7891,
    profile_id: "clash-gateway-7891",
    name: "Clash 端口 7891",
    proxy_url: "http://host.docker.internal:7891",
    current_node: "美国01",
  },
];

const listGateways = vi.fn();
const listNodes = vi.fn();
const switchGateway = vi.fn();

vi.mock("./AccountPoolApi", () => ({
  listAccountPoolProxyGateways: (...args: unknown[]) => listGateways(...args),
  listAccountPoolClashNodes: (...args: unknown[]) => listNodes(...args),
  switchAccountPoolProxyGateway: (...args: unknown[]) => switchGateway(...args),
}));

const renderPanel = (enabled = true) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProxyManagerPanel accessToken="token" enabled={enabled} />
    </QueryClientProvider>,
  );
};

describe("ProxyManagerPanel", () => {
  it("renders gateway rows with the current node and switches on selection", async () => {
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

    expect(await screen.findByText("当前节点：美国01")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: /Clash 端口 7891/ }));
    await user.click(await screen.findByRole("option", { name: "日本02" }));

    expect(switchGateway).toHaveBeenCalledWith("token", 7891, "日本02");
    expect(await screen.findByText("当前节点：日本02")).toBeInTheDocument();
  });

  it("reports a rejected switch and retains the observed current node", async () => {
    const user = userEvent.setup();
    listGateways.mockResolvedValue(gateways);
    listNodes.mockResolvedValue([{ name: "日本02", proxy_type: "Vmess" }]);
    switchGateway.mockRejectedValue(new Error("clash rejected the selection"));
    renderPanel();

    await user.click(await screen.findByRole("combobox", { name: /Clash 端口 7891/ }));
    await user.click(await screen.findByRole("option", { name: "日本02" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/切换失败|Unable to switch/i);
    expect(screen.getByText("当前节点：美国01")).toBeInTheDocument();
  });

  it("shows the Clash error hint when the manager cannot reach Clash", async () => {
    listGateways.mockRejectedValue(new Error("clash controller returned status 502"));
    renderPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Clash/);
  });

  it("renders nothing when disabled", () => {
    listGateways.mockResolvedValue([]);
    listNodes.mockResolvedValue([]);
    renderPanel(false);

    expect(screen.queryByTestId("proxy-manager-panel")).not.toBeInTheDocument();
  });
});
