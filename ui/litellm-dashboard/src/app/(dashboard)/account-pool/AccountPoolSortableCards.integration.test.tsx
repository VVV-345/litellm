import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountPoolSortableCards } from "./AccountPoolSortableCards";

const cards = [
  { id: "a", name: "Alpha" },
  { id: "b", name: "Beta" },
  { id: "c", name: "Gamma" },
];
const renderCards = (items = cards, supplier = "openai_codex") =>
  render(
    <AccountPoolSortableCards supplier={supplier} cards={items}>
      {(card) => <p>{card.name}</p>}
    </AccountPoolSortableCards>,
  );
const names = () =>
  within(screen.getByRole("list"))
    .getAllByRole("listitem")
    .map((item) => item.textContent);

describe("account pool card ordering", () => {
  beforeEach(() => window.localStorage.clear());

  it("reorders with the handle keyboard controls and restores the order on remount", () => {
    const view = renderCards();
    fireEvent.keyDown(screen.getByRole("button", { name: /Beta/ }), { key: "ArrowUp" });
    expect(names()).toEqual(["Beta", "Alpha", "Gamma"]);
    view.unmount();
    renderCards();
    expect(names()).toEqual(["Beta", "Alpha", "Gamma"]);
  });

  it("moves a dragged card to its target without changing another supplier", () => {
    const view = renderCards();
    const dataTransfer = { effectAllowed: "none", setData: vi.fn() };
    fireEvent.dragStart(screen.getByRole("button", { name: /Alpha/ }), { dataTransfer });
    fireEvent.drop(screen.getAllByRole("listitem")[2]);
    expect(names()).toEqual(["Beta", "Gamma", "Alpha"]);
    view.unmount();
    renderCards(cards, "kimi");
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("keeps hidden cards ordered when rearranging a filtered group", () => {
    const view = renderCards([cards[0], cards[2]]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Gamma/ }), { key: "ArrowUp" });
    expect(names()).toEqual(["Gamma", "Alpha"]);
    view.unmount();
    renderCards();
    expect(names()).toEqual(["Gamma", "Alpha", "Beta"]);
  });
});
