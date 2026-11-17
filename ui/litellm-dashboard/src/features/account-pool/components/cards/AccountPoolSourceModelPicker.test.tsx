/** 本文件验证资源侧模型勾选面板的多选、搜索、全选清空与 chip 移除行为。 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it } from "vitest";

import { AccountPoolSourceModelPicker } from "./AccountPoolSourceModelPicker";

const MODELS = ["gpt-4o", "qwen-max", "deepseek-chat"];

const Harness = ({ initial = [] }: { initial?: string[] }) => {
  const [selected, setSelected] = useState<string[]>(initial);
  return <AccountPoolSourceModelPicker models={MODELS} selected={selected} onChange={setSelected} />;
};

it("keeps every ticked model in the selection instead of replacing it", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  await user.click(screen.getByRole("checkbox", { name: "gpt-4o" }));
  await user.click(screen.getByRole("checkbox", { name: "qwen-max" }));

  expect(screen.getByRole("checkbox", { name: "gpt-4o" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "qwen-max" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "deepseek-chat" })).not.toBeChecked();
  expect(screen.getByText("已选 2 / 共 3")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "移除模型 gpt-4o" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "移除模型 qwen-max" })).toBeInTheDocument();
});

it("filters the rows by the search box and selects only the matches", async () => {
  const user = userEvent.setup();
  render(<Harness />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上游模型" }), { target: { value: "qwen" } });

  expect(screen.queryByRole("checkbox", { name: "gpt-4o" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "qwen-max" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "全选" }));
  expect(screen.getByRole("checkbox", { name: "qwen-max" })).toBeChecked();
  expect(screen.getByText("已选 1 / 共 3")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("textbox", { name: "搜索上游模型" }), { target: { value: "missing" } });
  expect(screen.getByText("没有匹配的模型")).toBeInTheDocument();
});

it("removes a model through its chip and clears the whole selection", async () => {
  const user = userEvent.setup();
  render(<Harness initial={["gpt-4o", "deepseek-chat"]} />);
  expect(screen.getByRole("checkbox", { name: "gpt-4o" })).toBeChecked();

  await user.click(screen.getByRole("button", { name: "移除模型 deepseek-chat" }));
  expect(screen.queryByRole("button", { name: "移除模型 deepseek-chat" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "deepseek-chat" })).not.toBeChecked();

  await user.click(screen.getByRole("button", { name: "清空" }));
  expect(screen.getByRole("checkbox", { name: "gpt-4o" })).not.toBeChecked();
  expect(screen.getByText("已选 0 / 共 3")).toBeInTheDocument();
});
