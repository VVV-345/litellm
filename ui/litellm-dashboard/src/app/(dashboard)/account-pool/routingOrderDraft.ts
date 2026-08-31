// 本文件管理路由拖拽的本地草稿，不会自行写入调度配置。

import type { RoutingTableEntry } from "./types";

type RoutingRouteIdentity = Pick<RoutingTableEntry, "account_id" | "deployment_id" | "billing_route_id" | "binding_id">;

export interface RoutingOrderDraft {
  scope: string;
  routeIds: string[];
  draggingRouteId: string | null;
}

export interface RoutingDraftMove {
  scope: string;
  sourceRouteIds: string[];
  sourceRouteId: string;
  targetRouteId: string;
}

export const emptyRoutingOrderDraft = (scope = ""): RoutingOrderDraft => ({
  scope,
  routeIds: [],
  draggingRouteId: null,
});

export const routeDraftId = (route: RoutingRouteIdentity): string =>
  route.binding_id ?? `${route.account_id}\u0000${route.deployment_id}\u0000${route.billing_route_id ?? ""}`;

export const buildRouteOrderScope = (model: string | null, routeIds: string[]): string =>
  `${model ?? ""}\u0001${routeIds.join("\u0001")}`;

export const draftForScope = (draft: RoutingOrderDraft, scope: string): RoutingOrderDraft =>
  draft.scope === scope ? draft : emptyRoutingOrderDraft(scope);

export const orderRoutes = <TRoute extends RoutingRouteIdentity>(routes: TRoute[], order: string[]): TRoute[] => {
  const byId = new Map(routes.map((route) => [routeDraftId(route), route]));
  const ordered = order.flatMap((routeId) => {
    const route = byId.get(routeId);
    return route ? [route] : [];
  });
  const orderedIds = new Set(order);
  return [...ordered, ...routes.filter((route) => !orderedIds.has(routeDraftId(route)))];
};

export const isSameRouteOrder = (left: string[], right: string[]): boolean =>
  left.length === right.length && left.every((routeId, index) => routeId === right[index]);

export const moveDraftRoute = (draft: RoutingOrderDraft, move: RoutingDraftMove): RoutingOrderDraft => {
  const current = draft.scope === move.scope && draft.routeIds.length > 0 ? draft.routeIds : move.sourceRouteIds;
  if (!current.includes(move.sourceRouteId) || !current.includes(move.targetRouteId)) return draft;
  const withoutSource = current.filter((routeId) => routeId !== move.sourceRouteId);
  const targetIndex = withoutSource.indexOf(move.targetRouteId);
  if (targetIndex === -1) return draft;
  return {
    scope: move.scope,
    routeIds: [...withoutSource.slice(0, targetIndex), move.sourceRouteId, ...withoutSource.slice(targetIndex)],
    draggingRouteId: null,
  };
};

export const startDraftDrag = (draft: RoutingOrderDraft, scope: string, routeId: string): RoutingOrderDraft => ({
  scope,
  routeIds: draft.scope === scope ? draft.routeIds : [],
  draggingRouteId: routeId,
});

export const endDraftDrag = (draft: RoutingOrderDraft, scope: string): RoutingOrderDraft =>
  draft.scope === scope ? { ...draft, draggingRouteId: null } : draft;
