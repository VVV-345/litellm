/** 验证 AdsPower 环境选择、导入结果及失败提示。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { AdsPowerSyncPanel } from "./AdsPowerSyncPanel";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/components/networking", () => ({
  apiClient: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) },
}));
const renderPanel = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AdsPowerSyncPanel accessToken="admin" />
    </QueryClientProvider>,
  );

describe("AdsPower manual sync", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18n.changeLanguage("en");
    get.mockResolvedValue({
      page: 1,
      has_more: false,
      profiles: [
        {
          id: "p1",
          name: "Default Profile",
          protocol: "socks5",
          host: "proxy.test",
          port: 12324,
          credentials_configured: true,
          syncable: true,
        },
        {
          id: "p2",
          name: "Direct",
          protocol: null,
          host: null,
          port: null,
          credentials_configured: false,
          syncable: false,
        },
      ],
    });
    post.mockResolvedValue({
      synced: [{ profile_id: "p1", isp_profile_id: "isp-1", name: "AdsPower [p1] Default Profile", created: true }],
    });
  });
  it("loads only on request and imports selected profiles without exposing credentials", async () => {
    renderPanel();
    expect(get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Load AdsPower profiles" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Default Profile/ }));
    expect(screen.getByRole("checkbox", { name: /Direct/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Sync selected proxies" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Synced 1 profile");
    expect(post).toHaveBeenCalledWith("/account_pool/adspower/sync", {
      accessToken: "admin",
      body: { profile_ids: ["p1"] },
    });
    expect(screen.getByText(/Select the imported ISP/)).toBeInTheDocument();
  });
  it("shows connection errors and permits retry", async () => {
    get.mockRejectedValue(new Error("AdsPower unavailable"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Load AdsPower profiles" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AdsPower unavailable");
    await waitFor(() => expect(screen.getByRole("button", { name: "Load AdsPower profiles" })).toBeEnabled());
  });
});
