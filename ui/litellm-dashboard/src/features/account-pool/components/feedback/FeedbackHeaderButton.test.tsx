import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { FeedbackHeaderButton } from "./FeedbackHeaderButton";

const auth = vi.hoisted(() => ({
  accessToken: "admin",
  userRole: "Admin",
  isViewOnly: false,
}));
vi.mock("@/app/(dashboard)/hooks/useAuthorized", () => ({ default: () => auth }));

beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
  auth.accessToken = "admin";
  auth.userRole = "Admin";
  auth.isViewOnly = false;
});

describe("feedback header access", () => {
  it("shows the entry for an administrator", () => {
    render(<FeedbackHeaderButton />);
    expect(screen.getByRole("button", { name: "新增反馈" })).toBeInTheDocument();
  });

  it("hides the entry from view-only and non-admin sessions", () => {
    auth.isViewOnly = true;
    const view = render(<FeedbackHeaderButton />);
    expect(screen.queryByRole("button", { name: "新增反馈" })).not.toBeInTheDocument();
    auth.isViewOnly = false;
    auth.userRole = "Internal User";
    view.rerender(<FeedbackHeaderButton />);
    expect(screen.queryByRole("button", { name: "新增反馈" })).not.toBeInTheDocument();
  });
});
