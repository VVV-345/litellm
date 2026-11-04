/** 验证摘要标签按需展开，并保留切换前尚未保存的表单。 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { Network, Settings2 } from "lucide-react";
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

it("keeps icon navigation accessible to keyboard users while panels stay collapsible", async () => {
  const user = userEvent.setup();
  render(
    <AccountPoolWorkspace
      title="模块"
      defaultOpen
      items={[
        { value: "network", label: "网络", summary: "2 个出口", icon: Network, content: <p>网络设置</p> },
        { value: "runtime", label: "运行", icon: Settings2, content: <p>运行设置</p> },
      ]}
    />,
  );
  const network = screen.getByRole("tab", { name: /^网络\s*2 个出口$/ });
  expect(network.querySelector('[data-slot="workspace-icon"]')).toBeVisible();
  expect(network.querySelector('[data-slot="workspace-icon"]')).toHaveAttribute("aria-hidden", "true");
  await user.click(network);
  await user.keyboard("{ArrowRight}{Enter}");
  expect(screen.getByRole("tab", { name: "运行" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("运行设置")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "收起模块" }));
  expect(screen.queryByRole("tabpanel")).not.toBeInTheDocument();
  expect(network.querySelector('[data-slot="workspace-icon"]')).toBeVisible();
});
