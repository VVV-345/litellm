"use client";

import { ColumnDef } from "@tanstack/react-table";
import type { TFunction } from "i18next";
import { Bot, Layers, MoreHorizontal, Server, Trash2 } from "lucide-react";

import { DataTableSortHeader } from "@/components/shared/DataTable";
import { DateCell, IdentityCell } from "@/components/shared/table_cells";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cva.config";

import { AccessGroup } from "./types";

interface ResourceTone {
  icon: typeof Layers;
  className: string;
}

const RESOURCE_TONES: Record<"models" | "mcpServers" | "agents", ResourceTone> = {
  models: { icon: Layers, className: "bg-info/10 text-info ring-blue-600/20" },
  mcpServers: { icon: Server, className: "bg-info/10 text-info ring-cyan-600/20" },
  agents: {
    icon: Bot,
    className:
      "bg-purple-50 text-purple-700 ring-purple-600/20 dark:bg-purple-950 dark:text-purple-300 dark:ring-purple-400/30",
  },
};

function ResourcesCell({ group, t }: { group: AccessGroup; t: TFunction }) {
  const items = [
    { key: "models" as const, label: t("ui.Models"), count: group.modelIds.length },
    { key: "mcpServers" as const, label: t("ui.MCP Servers"), count: group.mcpServerIds.length },
    { key: "agents" as const, label: t("ui.Agents"), count: group.agentIds.length },
  ];

  return (
    <div className="flex items-center gap-1.5">
      {items.map((item) => {
        const tone = RESOURCE_TONES[item.key];
        const Icon = tone.icon;
        return (
          <span
            key={item.key}
            title={`${item.count} ${item.label}`}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset [&_svg]:size-3.5",
              tone.className,
            )}
          >
            <Icon />
            <span className="tabular-nums">{item.count}</span>
          </span>
        );
      })}
    </div>
  );
}

function AccessGroupRowActions({
  group,
  onDeleteClick,
  t,
}: {
  group: AccessGroup;
  onDeleteClick: (group: AccessGroup) => void;
  t: TFunction;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("ui.Open access group actions")}
        data-testid={`access-group-actions-${group.id}`}
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground")}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem
          variant="destructive"
          data-testid="access-group-action-delete"
          onClick={() => onDeleteClick(group)}
        >
          <Trash2 />
          {t("ui.Delete access group")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface AccessGroupsTableColumnsDeps {
  canModify: boolean;
  onGroupClick: (id: string) => void;
  onDeleteClick: (group: AccessGroup) => void;
  t: TFunction;
}

export const getAccessGroupsTableColumns = ({
  canModify,
  onGroupClick,
  onDeleteClick,
  t,
}: AccessGroupsTableColumnsDeps): ColumnDef<AccessGroup>[] => {
  const columns: ColumnDef<AccessGroup>[] = [
    {
      id: "id",
      accessorKey: "id",
      meta: { title: t("ui.ID") },
      header: t("ui.ID"),
      size: 200,
      enableSorting: false,
      cell: ({ row }) => (
        <IdentityCell
          title={row.original.id}
          titleClassName="font-mono text-xs font-normal"
          onClick={() => onGroupClick(row.original.id)}
        />
      ),
    },
    {
      id: "name",
      accessorKey: "name",
      meta: { title: t("ui.Name") },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Name")} />,
      size: 220,
      enableSorting: true,
      cell: ({ row }) => {
        const name = row.original.name;
        return (
          <span className="block max-w-72 truncate text-sm font-medium" title={name}>
            {name || "-"}
          </span>
        );
      },
    },
    {
      id: "resources",
      meta: { title: t("ui.Resources") },
      header: t("ui.Resources"),
      size: 220,
      enableSorting: false,
      cell: ({ row }) => <ResourcesCell group={row.original} t={t} />,
    },
    {
      id: "createdAt",
      accessorKey: "createdAt",
      meta: { title: t("ui.Created") },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Created")} />,
      size: 150,
      enableSorting: true,
      sortingFn: "datetime",
      cell: ({ row }) => <DateCell value={row.original.createdAt} precision="date" />,
    },
    {
      id: "updatedAt",
      accessorKey: "updatedAt",
      meta: { title: t("ui.Updated") },
      header: t("ui.Updated"),
      size: 150,
      enableSorting: false,
      cell: ({ row }) => <DateCell value={row.original.updatedAt} precision="date" />,
    },
  ];

  if (!canModify) {
    return columns;
  }

  return [
    ...columns,
    {
      id: "actions",
      meta: { className: "text-right", headerClassName: "text-right" },
      header: () => <span className="sr-only">{t("ui.Actions")}</span>,
      size: 64,
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <AccessGroupRowActions group={row.original} onDeleteClick={onDeleteClick} t={t} />
        </div>
      ),
    },
  ];
};
