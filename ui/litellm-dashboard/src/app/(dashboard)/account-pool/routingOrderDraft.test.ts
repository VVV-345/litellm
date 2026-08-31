// 本文件验证路由拖拽只更新本地草稿，并可安全映射回当前路由列表。

import { describe, expect, it } from "vitest";

import {
  buildRouteOrderScope,
  draftForScope,
  emptyRoutingOrderDraft,
  isSameRouteOrder,
  moveDraftRoute,
  orderRoutes,
  routeDraftId,
  startDraftDrag,
} from "./routingOrderDraft";

const route = (bindingId: string) => ({
  account_id: `account-${bindingId}`,
  deployment_id: `deployment-${bindingId}`,
  billing_route_id: null,
  binding_id: bindingId,
});

describe("routing order draft", () => {
  it("keeps drag state local until a valid drop creates an ordered draft", () => {
    const routes = [route("a"), route("b"), route("c")];
    const sourceRouteIds = routes.map(routeDraftId);
    const scope = buildRouteOrderScope("model-a", sourceRouteIds);
    const dragging = startDraftDrag(emptyRoutingOrderDraft(), scope, "c");
    const move = {
      scope,
      sourceRouteIds,
      sourceRouteId: "c",
      targetRouteId: "a",
    };
    const moved = moveDraftRoute(dragging, move);

    expect(dragging.routeIds).toEqual([]);
    expect(moved.routeIds).toEqual(["c", "a", "b"]);
    expect(orderRoutes(routes, moved.routeIds).map(routeDraftId)).toEqual(["c", "a", "b"]);
    expect(isSameRouteOrder(moved.routeIds, sourceRouteIds)).toBe(false);
  });

  it("discards a stale draft when the selected model or source routes change", () => {
    const draft = {
      scope: buildRouteOrderScope("model-a", ["a", "b"]),
      routeIds: ["b", "a"],
      draggingRouteId: null,
    };
    const currentScope = buildRouteOrderScope("model-b", ["a", "b"]);

    expect(draftForScope(draft, currentScope)).toEqual(emptyRoutingOrderDraft(currentScope));
  });
});
