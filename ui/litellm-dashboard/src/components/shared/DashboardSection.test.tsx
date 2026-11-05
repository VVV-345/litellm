import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardSection } from "./DashboardSection";

describe("DashboardSection", () => {
  it("keeps an edited field when details are collapsed and reopened", () => {
    render(<DashboardSection title="Advanced filters" collapsible><input aria-label="Model" /></DashboardSection>);
    const toggle = screen.getByRole("button", { name: "Advanced filters" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("textbox", { name: "Model" })).not.toBeInTheDocument();
    fireEvent.click(toggle);
    fireEvent.change(screen.getByRole("textbox", { name: "Model" }), { target: { value: "selected-model" } });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("textbox", { name: "Model" })).toHaveValue("selected-model");
  });
});
