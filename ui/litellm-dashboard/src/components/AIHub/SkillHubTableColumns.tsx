"use client";

import { ColumnDef } from "@tanstack/react-table";
import type { TFunction } from "i18next";
import { Copy, ExternalLink, Info, MoreHorizontal } from "lucide-react";

import { DataTableSortHeader } from "@/components/shared/DataTable";
import { IdentityCell, StatusBadge } from "@/components/shared/table_cells";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cva.config";
import { copyToClipboard } from "@/utils/dataUtils";
import { translateUiText } from "@/utils/i18nText";
import { Plugin } from "@/components/claude_code_plugins/types";
import { useTranslation } from "react-i18next";

function getSkillSourceLink(skill: Plugin): { url: string; label: string } | null {
  const src = skill.source;
  if (src?.source === "github" && src.repo) {
    return { url: `https://github.com/${src.repo}`, label: src.repo };
  }
  if (src?.source === "git-subdir" && src.url) {
    const url = src.path ? `${src.url}/tree/main/${src.path}` : src.url;
    return { url, label: url.replace("https://github.com/", "") };
  }
  if (src?.source === "url" && src.url) {
    return { url: src.url, label: src.url.replace(/^https?:\/\//, "") };
  }
  return null;
}

interface SkillHubRowActionsProps {
  skill: Plugin;
  onSkillClick: (skill: Plugin) => void;
}

function SkillHubRowActions({ skill, onSkillClick }: SkillHubRowActionsProps) {
  const { t } = useTranslation();
  const ui = (value: string) => translateUiText(t, value);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={ui("Open skill actions")}
        data-testid={`skill-hub-actions-${skill.id}`}
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground")}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuItem data-testid="skill-hub-action-details" onClick={() => onSkillClick(skill)}>
          <Info />
          {ui("View details")}
        </DropdownMenuItem>
        <DropdownMenuItem
          data-testid="skill-hub-action-copy"
          onClick={() => void copyToClipboard(skill.name, ui("Skill name copied"))}
        >
          <Copy />
          {ui("Copy skill name")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface SkillHubTableColumnsDeps {
  onSkillClick: (skill: Plugin) => void;
  t?: TFunction;
}

export const getSkillHubTableColumns = ({
  onSkillClick,
  t,
}: SkillHubTableColumnsDeps): ColumnDef<Plugin>[] => {
  const ui = (value: string) => (t ? translateUiText(t, value) : value);
  return [
  {
    id: "name",
    accessorKey: "name",
    meta: { title: ui("Skill Name") },
    header: ({ column }) => <DataTableSortHeader column={column} title={ui("Skill Name")} />,
    size: 200,
    enableSorting: true,
    sortingFn: "alphanumeric",
    cell: ({ row }) => (
      <IdentityCell title={row.original.name} className="max-w-72" onClick={() => onSkillClick(row.original)} />
    ),
  },
  {
    id: "description",
    accessorKey: "description",
    meta: { title: ui("Description") },
    header: ui("Description"),
    size: 260,
    enableSorting: false,
    cell: ({ row }) => (
      <span className="block max-w-72 truncate text-xs" title={row.original.description || undefined}>
        {row.original.description || "-"}
      </span>
    ),
  },
  {
    id: "category",
    accessorKey: "category",
    meta: { title: ui("Category"), skeleton: "badge" },
    header: ({ column }) => <DataTableSortHeader column={column} title={ui("Category")} />,
    size: 130,
    enableSorting: true,
    sortingFn: "alphanumeric",
    cell: ({ row }) =>
      row.original.category ? (
        <Badge variant="secondary">{row.original.category}</Badge>
      ) : (
        <span className="text-xs text-muted-foreground">-</span>
      ),
  },
  {
    id: "domain",
    accessorKey: "domain",
    meta: { title: ui("Domain") },
    header: ({ column }) => <DataTableSortHeader column={column} title={ui("Domain")} />,
    size: 130,
    enableSorting: true,
    sortingFn: "alphanumeric",
    cell: ({ row }) => <span className="text-xs">{row.original.domain || "-"}</span>,
  },
  {
    id: "source",
    meta: { title: ui("Source") },
    header: ui("Source"),
    size: 200,
    enableSorting: false,
    cell: ({ row }) => {
      const link = getSkillSourceLink(row.original);
      if (!link) return <span className="text-xs text-muted-foreground">-</span>;
      return (
        <a
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex max-w-60 items-center gap-1 text-xs text-primary hover:underline"
          title={link.label}
        >
          <span className="truncate">{link.label}</span>
          <ExternalLink className="size-3 shrink-0" />
        </a>
      );
    },
  },
  {
    id: "enabled",
    accessorKey: "enabled",
    meta: { title: ui("Status"), skeleton: "badge" },
    header: ({ column }) => <DataTableSortHeader column={column} title={ui("Status")} />,
    size: 100,
    enableSorting: true,
    cell: ({ row }) => (
      <StatusBadge
        tone={row.original.enabled ? "success" : "neutral"}
        label={row.original.enabled ? ui("Public") : ui("Draft")}
      />
    ),
  },
  {
    id: "actions",
    meta: { className: "text-right", headerClassName: "text-right" },
    header: () => <span className="sr-only">{ui("Actions")}</span>,
    size: 64,
    enableSorting: false,
    enableHiding: false,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <SkillHubRowActions skill={row.original} onSkillClick={onSkillClick} />
      </div>
    ),
  },
];
};
