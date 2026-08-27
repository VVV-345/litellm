import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountPoolPanel, AccountPoolQueryState } from "./AccountPoolPanel";

describe("AccountPoolPanel", () => {
  it("renders the shared header, action and content structure", () => {
    render(
      <AccountPoolPanel title="渠道目录" description="2 个持久化渠道" action={<button>刷新</button>}>
        <p>渠道列表</p>
      </AccountPoolPanel>,
    );

    expect(screen.getByRole("heading", { name: "渠道目录" })).toBeInTheDocument();
    expect(screen.getByText("2 个持久化渠道")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
    expect(screen.getByText("渠道列表")).toBeInTheDocument();
  });
});

describe("AccountPoolQueryState", () => {
  it("exposes loading and error states to assistive technology", () => {
    const { rerender } = render(<AccountPoolQueryState kind="loading" message="正在读取渠道目录" />);

    expect(screen.getByRole("status")).toHaveTextContent("正在读取渠道目录");

    rerender(<AccountPoolQueryState kind="error" message="读取失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("读取失败");
  });
});
