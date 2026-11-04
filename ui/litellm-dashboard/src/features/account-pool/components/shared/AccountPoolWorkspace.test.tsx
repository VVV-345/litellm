/** 验证摘要标签按需展开，并保留切换前尚未保存的表单。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { AccountPoolWorkspace } from "./AccountPoolWorkspace";

it("opens one section, preserves drafts across tabs, and collapses its content", async () => {
  const user = userEvent.setup();
  render(
    <AccountPoolWorkspace
      title="测试工作区"
      items={[
        {
          value: "network",
          label: "出站网络",
          summary: "2 个节点",
          content: <input aria-label="草稿" defaultValue="" />,
        },
        { value: "runtime", label: "运行参数", summary: "3 项", content: <p>运行参数内容</p> },
      ]}
    />,
  );
  expect(screen.queryByRole("textbox", { name: "草稿" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: /出站网络/ }));
  await user.type(screen.getByRole("textbox", { name: "草稿" }), "未保存");
  await user.click(screen.getByRole("tab", { name: /运行参数/ }));
  expect(screen.getByText("运行参数内容")).toBeVisible();
  expect(screen.queryByRole("textbox", { name: "草稿" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: /出站网络/ }));
  expect(screen.getByRole("textbox", { name: "草稿" })).toHaveValue("未保存");
  await user.click(screen.getByRole("button", { name: "收起测试工作区" }));
  expect(screen.queryByRole("textbox", { name: "草稿" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "展开测试工作区" }));
  expect(screen.getByRole("textbox", { name: "草稿" })).toHaveValue("未保存");
});
