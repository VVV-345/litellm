/** 验证 GLM 标准 Key 预设复用兼容创建表单；仅替换网络边界。 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { apiClient } from "@/components/networking";
import { AccountPoolOpenAICompatibleForm } from "./AccountPoolOpenAICompatibleForm";

vi.mock("@/components/networking", () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));
beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("zh-CN");
  vi.mocked(apiClient.get).mockResolvedValue([]);
  vi.mocked(apiClient.post).mockResolvedValue({ id: "glm-card" });
});

it.each([
  ["GLM（智谱国内站）", "https://open.bigmodel.cn/api/paas/v4"],
  ["GLM（Z.AI 国际站）", "https://api.z.ai/api/paas/v4"],
])("%s fetches from the selected resource without creating or automatically selecting a model", async (label, base) => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  vi.mocked(apiClient.post).mockResolvedValueOnce(["glm-fixture-a", "glm-fixture-b"]);
  render(<AccountPoolOpenAICompatibleForm mode="glm" accessToken="fixture-admin" onClose={vi.fn()} onCreated={onCreated} />);
  fireEvent.change(screen.getByLabelText("卡片名称"), { target: { value: "GLM test" } });
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "fixture-standard-key" } });
  await user.click(screen.getByRole("combobox", { name: "接入站点" }));
  await user.click(await screen.findByRole("option", { name: label }));
  expect(screen.getByLabelText("服务地址")).toHaveValue(base);
  expect(screen.getByLabelText("服务地址")).toHaveAttribute("readonly");
  expect(screen.getByText(/仅支持标准 API Key/)).toBeInTheDocument();
  expect(apiClient.post).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "从资源侧获取" }));
  await waitFor(() => expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
    "/account_pool/openai-compatible/models",
    expect.objectContaining({
      body: { base_url: base, credential: { api_key: "fixture-standard-key", weight: 1, proxy_profile_id: undefined }, headers: [] },
    }),
  ));
  expect(screen.getByLabelText("测试模型")).toHaveValue("");
  expect(screen.getByLabelText("自定义模型（逗号分隔）")).toHaveValue("");
  await user.click(await screen.findByRole("combobox", { name: "添加上游模型" }));
  await user.click(await screen.findByRole("option", { name: "glm-fixture-b" }));
  expect(screen.getByLabelText("自定义模型（逗号分隔）")).toHaveValue("glm-fixture-b");
  expect(screen.getByLabelText("测试模型")).toHaveValue("glm-fixture-b");
  await user.click(screen.getByRole("button", { name: i18n.t("accountPool.providers.create") }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  const expectedConfig = {
    base_url: base, test_model: "glm-fixture-b", custom_models: ["glm-fixture-b"],
    api_keys: [{ api_key: "fixture-standard-key", weight: 1, proxy_profile_id: undefined }],
  };
  const expectedRequest = {
    provider: "openai", channel: "openai_compatible", supplier: "openai_compatible",
    provider_family: "openai_compatible", openai_compatible: expect.objectContaining(expectedConfig),
  };
  expect(apiClient.post).toHaveBeenCalledWith("/account_pool/openai-compatible", expect.objectContaining({
    body: expect.objectContaining(expectedRequest),
  }));
});

it("lets generic compatibility fetch with its selected proxy and headers, preserving manual entry on failure", async () => {
  const user = userEvent.setup();
  vi.mocked(apiClient.get).mockResolvedValue([{ id: "fixture-proxy", name: "测试代理" }]);
  vi.mocked(apiClient.post).mockRejectedValueOnce(new Error("fixture-upstream-rejection"));
  render(<AccountPoolOpenAICompatibleForm mode="compatible" accessToken="fixture-admin" onClose={vi.fn()} onCreated={vi.fn()} />);
  expect(screen.queryByText("接入站点")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("服务地址"), { target: { value: "https://fixture.example" } });
  fireEvent.change(screen.getByLabelText("测试模型"), { target: { value: "custom-test" } });
  fireEvent.change(screen.getByLabelText("自定义模型（逗号分隔）"), { target: { value: "custom-a, custom-b" } });
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "fixture-secret" } });
  await user.click(screen.getByRole("combobox", { name: i18n.t("accountPool.providers.openaiCompatible.proxyProfileId") }));
  await user.click(await screen.findByRole("option", { name: "测试代理" }));
  await user.click(screen.getByText("高级连接设置"));
  await user.click(screen.getByRole("button", { name: i18n.t("accountPool.providers.openaiCompatible.addHeader") }));
  fireEvent.change(screen.getByLabelText(i18n.t("accountPool.providers.openaiCompatible.headerName", { index: 1 })), {
    target: { value: "X-Fixture" },
  });
  fireEvent.change(screen.getByLabelText(i18n.t("accountPool.providers.openaiCompatible.headerValue", { index: 1 })), {
    target: { value: "fixture-value" },
  });
  await user.click(screen.getByRole("button", { name: "从资源侧获取" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("可手动填写模型");
  expect(apiClient.post).toHaveBeenCalledExactlyOnceWith(
    "/account_pool/openai-compatible/models",
    expect.objectContaining({ body: {
      base_url: "https://fixture.example/v1",
      credential: { api_key: "fixture-secret", weight: 1, proxy_profile_id: "fixture-proxy" },
      headers: [["X-Fixture", "fixture-value"]],
    } }),
  );
  expect(screen.getByLabelText("服务地址")).not.toHaveAttribute("readonly");
  expect(screen.getByLabelText("测试模型")).toHaveValue("custom-test");
  expect(screen.getByLabelText("自定义模型（逗号分隔）")).toHaveValue("custom-a, custom-b");
  expect(screen.getByLabelText("API Key")).toHaveAttribute("type", "password");
});
