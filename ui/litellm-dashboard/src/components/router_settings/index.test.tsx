import i18n from "@/i18n";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "../../../tests/test-utils";
import userEvent from "@testing-library/user-event";
import RouterSettings from "./index";

vi.mock("@/features/account-pool/hooks/useAccountPoolQuery", () => ({
  useAccountPoolQuery: () => ({ data: [
    { id: "11111111-1111-1111-1111-111111111111", name: "Card A", enabled_models: ["gpt-6-astra"], status: "ready" },
    { id: "22222222-2222-2222-2222-222222222222", name: "Card B", enabled_models: ["gpt-6-astra"], status: "ready" },
  ] }),
}));

// The strategy select only renders once getRouterSettingsCall resolves, so awaiting it is how a
// test knows the loaded settings are on screen.
const findStrategySelect = () => screen.findByRole("combobox", { name: "Routing Strategy" });

vi.mock("@/components/networking", () => ({
  getCallbacksCall: vi.fn(),
  getRouterSettingsCall: vi.fn(),
  setCallbacksCall: vi.fn(),
}));

import { getCallbacksCall, getRouterSettingsCall, setCallbacksCall } from "@/components/networking";
import { toast } from "@/lib/toast";

const mockCallbacksResponse = {
  router_settings: {
    routing_strategy: "simple-shuffle",
    num_retries: 3,
    timeout: 30,
  },
};

const mockRouterSettingsResponse = {
  fields: [
    {
      field_name: "routing_strategy",
      ui_field_name: "Routing Strategy",
      field_description: "How requests are distributed",
      options: ["simple-shuffle", "latency-based-routing"],
      link: null,
    },
    {
      field_name: "enable_tag_filtering",
      ui_field_name: "Tag Filtering",
      field_description: "Route by tag",
      field_value: false,
      link: null,
    },
  ],
  routing_strategy_descriptions: {
    "simple-shuffle": "Randomly pick a deployment",
    "latency-based-routing": "Pick the lowest-latency deployment",
  },
};

const defaultProps = {
  accessToken: "test-token",
  userRole: "Admin",
  userID: "user-1",
  modelData: null,
};

describe("RouterSettings", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en-US");
    vi.clearAllMocks();
    vi.mocked(getCallbacksCall).mockResolvedValue(mockCallbacksResponse);
    vi.mocked(getRouterSettingsCall).mockResolvedValue(mockRouterSettingsResponse);
    vi.mocked(setCallbacksCall).mockResolvedValue({});
  });

  it("should render nothing when accessToken is null", () => {
    const { container } = renderWithProviders(<RouterSettings {...defaultProps} accessToken={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("should render the Save Changes and Reset buttons when authenticated", () => {
    renderWithProviders(<RouterSettings {...defaultProps} />);
    expect(screen.getByRole("button", { name: /save changes/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reset/i })).toBeInTheDocument();
  });

  it("should fetch callbacks and router settings on mount", async () => {
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await waitFor(() => {
      expect(getCallbacksCall).toHaveBeenCalledWith("test-token", "user-1", "Admin");
    });
    expect(getRouterSettingsCall).toHaveBeenCalledWith("test-token");
  });

  it("should not fetch data when any required prop is missing", () => {
    renderWithProviders(<RouterSettings {...defaultProps} userRole={null} />);
    expect(getCallbacksCall).not.toHaveBeenCalled();
  });

  it("should render routing strategies loaded from the API", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await user.click(await findStrategySelect());

    expect(await screen.findByRole("option", { name: /simple-shuffle/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /latency-based-routing/ })).toBeInTheDocument();
  });

  it("should call setCallbacksCall with updated settings on Save Changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await findStrategySelect();

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(setCallbacksCall).toHaveBeenCalledWith(
      "test-token",
      expect.objectContaining({
        router_settings: expect.objectContaining({
          routing_strategy: "simple-shuffle",
        }),
      }),
    );
  });

  it("should send the edited input value, not the loaded one, on Save Changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await findStrategySelect();

    const numRetries = await screen.findByRole("textbox", { name: /num_retries/i });
    await user.clear(numRetries);
    fireEvent.change(numRetries, { target: { value: "42" } });

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(setCallbacksCall).toHaveBeenCalledWith(
        "test-token",
        expect.objectContaining({
          router_settings: expect.objectContaining({ num_retries: 42 }),
        }),
      ),
    );
  });

  it("should show a success notification after saving", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await findStrategySelect();
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(toast.success).toHaveBeenCalledWith("router settings updated successfully");
  });

  it("should not render or save routing_groups (owned by the Routing Groups tab)", async () => {
    const user = userEvent.setup();
    vi.mocked(getCallbacksCall).mockResolvedValue({
      router_settings: {
        routing_strategy: "simple-shuffle",
        num_retries: 3,
        routing_groups: [{ group_name: "g1", models: ["gpt-4"], routing_strategy: "simple-shuffle" }],
      },
    });
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await findStrategySelect();
    expect(document.querySelector('input[name="routing_groups"]')).toBeNull();

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(setCallbacksCall).toHaveBeenCalledWith("test-token", {
        router_settings: expect.not.objectContaining({ routing_groups: expect.anything() }),
      }),
    );
  });

  it("should surface an error and not claim success when saving fails", async () => {
    const user = userEvent.setup();
    vi.mocked(setCallbacksCall).mockRejectedValue(new Error("422 Unprocessable Entity"));
    renderWithProviders(<RouterSettings {...defaultProps} />);

    await findStrategySelect();
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(toast.fromError).toHaveBeenCalled();
    });
    expect(toast.success).not.toHaveBeenCalled();
  });
  it("saves account selection and native session settings as a structured value", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);
    await findStrategySelect();
    await user.click(screen.getByRole("switch", { name: "同一会话优先使用同一卡片" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "会话亲和性有效期（秒）" }), { target: { value: "900" } });
    await user.click(screen.getByRole("combobox", { name: "同优先级卡片偏好" }));
    await user.click(await screen.findByRole("option", { name: "优先高等级套餐" }));
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(setCallbacksCall).toHaveBeenCalledWith("test-token", {
        router_settings: expect.objectContaining({
          account_pool_routing: {
            selection: "plan",
            preferred_account_ids: [],
            session_affinity: true,
            session_affinity_ttl_seconds: 900,
          },
        }),
      }),
    );
  });

  it("saves the chosen card order after moving a card earlier", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RouterSettings {...defaultProps} />);
    await findStrategySelect();
    await user.click(screen.getByRole("combobox", { name: "同优先级卡片偏好" }));
    await user.click(await screen.findByRole("option", { name: "按指定卡片顺序" }));
    for (const name of [/Card A/, /Card B/]) {
      await user.click(screen.getByRole("combobox", { name: "添加优先卡片" }));
      await user.click(await screen.findByRole("option", { name }));
    }
    await user.click(screen.getAllByRole("button", { name: "上移" })[1]);
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(setCallbacksCall).toHaveBeenCalledWith("test-token", {
      router_settings: expect.objectContaining({ account_pool_routing: expect.objectContaining({
        selection: "ordered",
        preferred_account_ids: ["22222222-2222-2222-2222-222222222222", "11111111-1111-1111-1111-111111111111"],
      }) }),
    }));
  });
});
