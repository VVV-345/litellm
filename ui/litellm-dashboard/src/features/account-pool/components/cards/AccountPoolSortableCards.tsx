import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { GripVertical } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const mergeCardOrder = (order: readonly string[], cardIds: readonly string[]): readonly string[] => [
  ...new Set([...order, ...cardIds]),
];

export function AccountPoolSortableCards<T extends { id: string; name: string }>({
  supplier,
  cards,
  children,
}: {
  supplier: string;
  cards: readonly T[];
  children: (card: T) => ReactNode;
}) {
  const { t } = useTranslation();
  const storageKey = `account-pool.card-order.${supplier}`;
  const readOrder = (): readonly string[] => {
    if (typeof window === "undefined") return [];
    try {
      const stored: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
      return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
    } catch {
      return [];
    }
  };
  const [order, setOrder] = useState<readonly string[]>(() =>
    mergeCardOrder(
      readOrder(),
      cards.map((card) => card.id),
    ),
  );
  const hydrated = useSyncExternalStore(
    () => () => undefined,
    () => true,
    () => false,
  );
  const [dragged, setDragged] = useState<string | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [storageFailed, setStorageFailed] = useState(false);

  const cardIds = useMemo(() => cards.map((card) => card.id), [cards]);
  const resolvedOrder = useMemo(() => (hydrated ? mergeCardOrder(order, cardIds) : []), [cardIds, hydrated, order]);
  const orderIndex = useMemo(() => new Map(resolvedOrder.map((id, index) => [id, index])), [resolvedOrder]);
  const ordered = useMemo(
    () =>
      [...cards].sort(
        (left, right) =>
          (orderIndex.get(left.id) ?? resolvedOrder.length) - (orderIndex.get(right.id) ?? resolvedOrder.length),
      ),
    [cards, orderIndex, resolvedOrder.length],
  );
  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(resolvedOrder));
    } catch {
      return;
    }
  }, [hydrated, resolvedOrder, storageKey]);
  const move = (sourceId: string, targetId: string) => {
    const sourceIndex = ordered.findIndex((card) => card.id === sourceId);
    const targetIndex = ordered.findIndex((card) => card.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return;
    const withoutSource = ordered.filter((card) => card.id !== sourceId);
    const nextVisible = [
      ...withoutSource.slice(0, targetIndex),
      ordered[sourceIndex],
      ...withoutSource.slice(targetIndex),
    ].map((card) => card.id);
    const visibleIds = new Set(cards.map((card) => card.id));
    const previous = mergeCardOrder(order, cardIds);
    const visibleSlots = previous.filter((id) => visibleIds.has(id));
    const replacements = new Map(visibleSlots.map((id, index) => [id, nextVisible[index] ?? id]));
    const next = previous.map((id) => replacements.get(id) ?? id);
    setOrder(next);
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(next));
      setStorageFailed(false);
    } catch {
      setStorageFailed(true);
    }
  };
  const handleDragOver = (event: React.DragEvent<HTMLLIElement>, cardId: string) => {
    if (!dragged) return;
    event.preventDefault();
    setTarget(cardId);
  };
  const handleDrop = (event: React.DragEvent<HTMLLIElement>, cardId: string) => {
    event.preventDefault();
    if (dragged) move(dragged, cardId);
    setDragged(null);
    setTarget(null);
  };
  const handleDragStart = (event: React.DragEvent<HTMLButtonElement>, cardId: string) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", cardId);
    setDragged(cardId);
  };
  const finishDrag = () => {
    setDragged(null);
    setTarget(null);
  };
  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, cardId: string, index: number) => {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const next = ordered[index + (["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1)];
    if (next) move(cardId, next.id);
  };

  return (
    <div className="grid gap-2">
      <p className="text-xs text-muted-foreground">{t("accountPool.dashboard.reorderHint")}</p>
      {storageFailed && (
        <p role="status" className="text-xs text-destructive">
          {t("accountPool.dashboard.orderNotSaved")}
        </p>
      )}
      <ul className="m-0 grid list-none grid-cols-1 items-start gap-4 p-0 md:grid-cols-2 2xl:grid-cols-3">
        {!hydrated &&
          cards.map((card) => (
            <li key={card.id} aria-hidden="true" className="relative min-w-0 rounded-xl">
              <div className="invisible">{children(card)}</div>
              <Skeleton className="absolute inset-0 h-full w-full rounded-xl" />
            </li>
          ))}
        {hydrated &&
          ordered.map((card, index) => (
            <li
              key={card.id}
              className={`relative min-w-0 rounded-xl transition-shadow ${dragged === card.id ? "opacity-50" : ""} ${target === card.id && dragged !== card.id ? "ring-2 ring-primary" : ""}`}
              onDragOver={(event) => handleDragOver(event, card.id)}
              onDrop={(event) => handleDrop(event, card.id)}
            >
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="absolute right-3 top-3 z-raised cursor-grab touch-none active:cursor-grabbing"
                draggable
                aria-label={t("accountPool.dashboard.reorderCard", { name: card.name })}
                title={t("accountPool.dashboard.reorderHint")}
                onDoubleClick={(event) => event.stopPropagation()}
                onDragStart={(event) => handleDragStart(event, card.id)}
                onDragEnd={finishDrag}
                onKeyDown={(event) => handleKeyDown(event, card.id, index)}
              >
                <GripVertical className="size-4" />
              </Button>
              {children(card)}
            </li>
          ))}
      </ul>
    </div>
  );
}
