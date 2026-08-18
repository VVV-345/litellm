// 本文件验证模型选择器能够接收上游列表之外的自定义模型名。
import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CreatableModelSelect from "./CreatableModelSelect";

describe("CreatableModelSelect", () => {
  it("creates a model that is absent from the discovered list", async () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <CreatableModelSelect models={["glm-4.7"]} placeholder="选择模型" onChange={onChange} />,
    );

    const input = getByRole("combobox");
    fireEvent.change(input, { target: { value: "glm-future-model" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() => expect(onChange).toHaveBeenCalledWith(["glm-future-model"]));
  });
});
