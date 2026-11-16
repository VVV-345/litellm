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
])("%s only fills the standard base, keeps manual models and uses the existing create API", async (label, base) => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  render(<AccountPoolOpenAICompatibleForm accessToken="fixture-admin" onClose={vi.fn()} onCreated={onCreated} />);
  fireEvent.change(screen.getByLabelText("卡片名称"), { target: { value: "GLM test" } });
  fireEvent.change(screen.getByLabelText("API Key 1"), { target: { value: "fixture-standard-key" } });
  await user.click(screen.getByRole("combobox", { name: "接入预设" }));
  await user.click(await screen.findByRole("option", { name: label }));
  expect(screen.getByLabelText("服务地址")).toHaveValue(base);
  expect(screen.getByLabelText("服务地址")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("测试模型")).toHaveValue("");
  expect(screen.getByLabelText("自定义模型（逗号分隔）")).toHaveValue("");
  expect(screen.queryByRole("button", { name: "获取可选测试模型" })).not.toBeInTheDocument();
  expect(screen.getByText(/仅支持标准 API Key/)).toBeInTheDocument();
  expect(apiClient.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("测试模型"), { target: { value: "glm-5.3" } });
  await user.click(screen.getByRole("button", { name: i18n.t("accountPool.providers.create") }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  const expectedConfig = {
    base_url: base, test_model: "glm-5.3", custom_models: [],
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

it("switching the preset never replaces an entered Key or model list and keeps the generic entry", async () => {
  const user = userEvent.setup();
  render(<AccountPoolOpenAICompatibleForm accessToken="fixture-admin" onClose={vi.fn()} onCreated={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("测试模型"), { target: { value: "custom-test" } });
  fireEvent.change(screen.getByLabelText("自定义模型（逗号分隔）"), { target: { value: "custom-a, custom-b" } });
  fireEvent.change(screen.getByLabelText("API Key 1"), { target: { value: "fixture-secret" } });
  await user.click(screen.getByRole("combobox", { name: "接入预设" }));
  await user.click(await screen.findByRole("option", { name: "GLM（智谱国内站）" }));
  await user.click(screen.getByRole("combobox", { name: "接入预设" }));
  await user.click(await screen.findByRole("option", { name: "通用 OpenAI 兼容" }));
  expect(screen.getByLabelText("服务地址")).toHaveValue("");
  expect(screen.getByLabelText("服务地址")).not.toHaveAttribute("readonly");
  expect(screen.getByLabelText("测试模型")).toHaveValue("custom-test");
  expect(screen.getByLabelText("自定义模型（逗号分隔）")).toHaveValue("custom-a, custom-b");
  expect(screen.getByLabelText("API Key 1")).toHaveValue("fixture-secret");
  expect(screen.getByLabelText("API Key 1")).toHaveAttribute("type", "password");
  expect(apiClient.post).not.toHaveBeenCalled();
});
