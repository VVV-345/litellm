// 本文件验证添加模型表单的供应商差异和自定义模型输入行为。
import { fireEvent, render, waitFor } from "@testing-library/react";
import { Form } from "antd";
import { describe, expect, it } from "vitest";
import { getPlaceholder, Providers } from "../provider_info_helpers";
import LiteLLMModelNameField from "./litellm_model_name";

describe("LitellmModelNameField", () => {
  it("should render", () => {
    const { getByText } = render(
      <Form>
        <LiteLLMModelNameField
          selectedProvider={Providers.OpenAI}
          providerModels={[]}
          getPlaceholder={getPlaceholder}
        />
      </Form>,
    );
    expect(getByText("LiteLLM Model Name(s)")).toBeInTheDocument();
  });

  it("should show Azure placeholder as 'my-deployment'", () => {
    const { getByPlaceholderText, queryByPlaceholderText } = render(
      <Form>
        <LiteLLMModelNameField selectedProvider={Providers.Azure} providerModels={[]} getPlaceholder={getPlaceholder} />
      </Form>,
    );
    expect(getByPlaceholderText("my-deployment")).toBeInTheDocument();
    expect(queryByPlaceholderText("gpt-3.5-turbo")).toBeNull();
  });

  it("allows a model missing from the price map to be entered directly", async () => {
    const formRef = { current: null as ReturnType<typeof Form.useForm>[0] | null };

    const Harness = () => {
      const [form] = Form.useForm();
      formRef.current = form;
      return (
        <Form form={form}>
          <LiteLLMModelNameField
            selectedProvider={Providers.OpenAI}
            providerModels={["gpt-4.1"]}
            getPlaceholder={getPlaceholder}
          />
        </Form>
      );
    };

    const { getByRole } = render(<Harness />);
    const input = getByRole("combobox");
    const customModelMapping = {
      public_name: "provider-new-model",
      litellm_model: "provider-new-model",
    };
    const enterKey = { key: "Enter", code: "Enter", keyCode: 13, which: 13 };
    fireEvent.change(input, { target: { value: "provider-new-model" } });
    fireEvent.keyDown(input, enterKey);

    await waitFor(() => {
      expect(formRef.current?.getFieldValue("model")).toContain("provider-new-model");
      expect(formRef.current?.getFieldValue("model_mappings")).toContainEqual(customModelMapping);
    });
  });
});
