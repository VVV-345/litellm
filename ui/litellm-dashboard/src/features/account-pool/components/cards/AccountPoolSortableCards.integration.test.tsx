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

  it("uses four desktop columns with one mobile column and two tablet columns", () => {
    renderCards([...cards, { id: "d", name: "Delta" }]);

    expect(screen.getByRole("list")).toHaveClass("grid-cols-1", "md:grid-cols-2", "xl:grid-cols-4");
    expect(screen.getByRole("list")).not.toHaveClass("2xl:grid-cols-3");
    expect(names()).toEqual(["Alpha", "Beta", "Gamma", "Delta"]);
  });

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

  it("keeps the established order when refreshed data arrives in a different order", () => {
    const view = renderCards();
    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);

    view.rerender(
      <AccountPoolSortableCards supplier="openai_codex" cards={[cards[2], cards[1], cards[0]]}>
        {(card) => <p>{card.name}</p>}
      </AccountPoolSortableCards>,
    );

    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("appends newly discovered cards without moving existing cards", () => {
    const view = renderCards(cards.slice(0, 2));
    expect(names()).toEqual(["Alpha", "Beta"]);

    view.rerender(
      <AccountPoolSortableCards supplier="openai_codex" cards={[cards[2], cards[1], cards[0]]}>
        {(card) => <p>{card.name}</p>}
      </AccountPoolSortableCards>,
    );

    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("repairs a same-length stored order when its contents are invalid", () => {
    window.localStorage.setItem("account-pool.card-order.openai_codex", JSON.stringify(["a", "a", "b"]));

    renderCards();

    expect(names()).toEqual(["Alpha", "Beta", "Gamma"]);
    expect(JSON.parse(window.localStorage.getItem("account-pool.card-order.openai_codex") ?? "[]")).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("keeps the in-memory order stable when local storage cannot be written", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Storage disabled", "SecurityError");
    });
    const view = renderCards();

    fireEvent.keyDown(screen.getByRole("button", { name: /Beta/ }), { key: "ArrowUp" });
    expect(names()).toEqual(["Beta", "Alpha", "Gamma"]);
    expect(screen.getByRole("status")).toHaveTextContent(/无法保存|could not be saved/i);

    view.rerender(
      <AccountPoolSortableCards supplier="openai_codex" cards={[cards[2], cards[1], cards[0]]}>
        {(card) => <p>{card.name}</p>}
      </AccountPoolSortableCards>,
    );
    expect(names()).toEqual(["Beta", "Alpha", "Gamma"]);
    setItem.mockRestore();
  });

  it("reorders only the current page without moving cards on hidden pages", () => {
    const allCards = [...cards, { id: "d", name: "Delta" }, { id: "e", name: "Epsilon" }];
    render(
      <AccountPoolSortableCards supplier="openai_codex" cards={allCards} pagination={{ page: 2, pageSize: 2, onPageChange: vi.fn() }}>
        {(card) => <p>{card.name}</p>}
      </AccountPoolSortableCards>,
    );
    expect(names()).toEqual(["Gamma", "Delta"]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Delta/ }), { key: "ArrowUp" });
    expect(names()).toEqual(["Delta", "Gamma"]);
    expect(JSON.parse(window.localStorage.getItem("account-pool.card-order.openai_codex") ?? "[]")).toEqual(["a", "b", "d", "c", "e"]);
    fireEvent.keyDown(screen.getByRole("button", { name: /Delta/ }), { key: "ArrowUp" });
    expect(names()).toEqual(["Delta", "Gamma"]);
  });
});
