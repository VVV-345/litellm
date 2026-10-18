import { render, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";
import i18next from "@/i18n";
import LoadingScreen from "./LoadingScreen";

describe("LoadingScreen", () => {
  beforeEach(async () => {
    await i18next.changeLanguage("en");
  });

  it("should render", () => {
    render(<LoadingScreen />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });
});
