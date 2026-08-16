"use client";

import { ColumnDef } from "@tanstack/react-table";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Copy, Info, KeyRound, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import { UserInfo } from "@/components/networking";
import { createSelectionColumn, DataTableSortHeader } from "@/components/shared/DataTable";
import { CellTooltip, DateCell, IdentityCell, MoneyCell, StatusBadge } from "@/components/shared/table_cells";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cva.config";
import { copyToClipboard } from "@/utils/dataUtils";

const SSO_ID_HINT =
  "SSO ID is the ID of the user in the SSO provider. If the user is not using SSO, this will be null.";

const SCIM_INACTIVE_HINT = "Deactivated via SCIM (external identity provider). The user's virtual keys are blocked.";

function isScimInactive(user: UserInfo): boolean {
  return (user.metadata as Record<string, unknown> | null | undefined)?.scim_active === false;
}
interface UserRowActionsProps {
  user: UserInfo;
  onUserClick: (userId: string, openInEditMode?: boolean) => void;
  onDeleteUser: (user: UserInfo) => void;
  onResetPassword: (userId: string) => void;
}

function UserRowActions({ user, onUserClick, onDeleteUser, onResetPassword }: UserRowActionsProps) {
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("ui.Open user actions")}
        data-testid={`user-actions-${user.user_id}`}
        className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "text-muted-foreground")}
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onClick={() => onUserClick(user.user_id, true)} data-testid="user-action-edit">
          <Pencil />
          {t("ui.Edit user")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onResetPassword(user.user_id)} data-testid="user-action-reset-password">
          <KeyRound />
          {t("ui.Reset password")}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => void copyToClipboard(user.user_id, t("ui.User ID copied"))}
          data-testid="user-action-copy"
        >
          <Copy />
          {t("ui.Copy user ID")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={() => onDeleteUser(user)} data-testid="user-action-delete">
          <Trash2 />
          {t("ui.Delete user")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export interface UsersTableColumnsDeps {
  possibleUIRoles: Record<string, Record<string, string>> | null;
  includeSelection: boolean;
  onUserClick: (userId: string, openInEditMode?: boolean) => void;
  onDeleteUser: (user: UserInfo) => void;
  onResetPassword: (userId: string) => void;
  t: TFunction;
}

export const getUsersTableColumns = ({
  possibleUIRoles,
  includeSelection,
  onUserClick,
  onDeleteUser,
  onResetPassword,
  t,
}: UsersTableColumnsDeps): ColumnDef<UserInfo>[] => {
  const baseColumns: ColumnDef<UserInfo>[] = [
    {
      id: "user_id",
      accessorKey: "user_id",
      meta: { title: t("ui.User ID") },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.User ID")} variant="header-cycle" />,
      size: 220,
      enableSorting: true,
      cell: ({ row }) => (
        <IdentityCell
          title={row.original.user_id}
          titleClassName="font-mono text-xs text-primary"
          onClick={() => onUserClick(row.original.user_id, false)}
        />
      ),
    },
    {
      id: "user_email",
      accessorKey: "user_email",
      meta: { title: t("ui.Email") },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Email")} variant="header-cycle" />,
      size: 220,
      enableSorting: true,
      cell: ({ row }) => (
        <span className="block max-w-60 truncate text-sm" title={row.original.user_email ?? undefined}>
          {row.original.user_email || "-"}
        </span>
      ),
    },
    {
      id: "status",
      meta: { title: t("ui.Status"), skeleton: "badge" },
      header: t("ui.Status"),
      size: 110,
      enableSorting: false,
      cell: ({ row }) => {
        if (isScimInactive(row.original)) {
          return (
            <StatusBadge
              tone="error"
              label={t("ui.Inactive")}
              tooltip={t(SCIM_INACTIVE_HINT)}
              dataTestId={`user-status-${row.original.user_id}`}
            />
          );
        }
        return <StatusBadge tone="success" label={t("ui.Active")} dataTestId={`user-status-${row.original.user_id}`} />;
      },
    },
    {
      id: "user_role",
      accessorKey: "user_role",
      meta: { title: t("ui.Global Proxy Role") },
      header: ({ column }) => (
        <DataTableSortHeader column={column} title={t("ui.Global Proxy Role")} variant="header-cycle" />
      ),
      size: 160,
      enableSorting: true,
      cell: ({ row }) => <span className="text-sm">{possibleUIRoles?.[row.original.user_role]?.ui_label || "-"}</span>,
    },
    {
      id: "user_alias",
      accessorKey: "user_alias",
      meta: { title: t("ui.User Alias") },
      header: t("ui.User Alias"),
      size: 150,
      enableSorting: false,
      cell: ({ row }) => (
        <span className="block max-w-40 truncate text-sm" title={row.original.user_alias ?? undefined}>
          {row.original.user_alias || "-"}
        </span>
      ),
    },
    {
      id: "spend",
      accessorKey: "spend",
      meta: { title: t("ui.Spend (USD)"), numeric: true },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Spend (USD)")} variant="header-cycle" />,
      size: 130,
      enableSorting: true,
      cell: ({ row }) => <MoneyCell value={row.original.spend} decimals={2} />,
    },
    {
      id: "max_budget",
      accessorKey: "max_budget",
      meta: { title: t("ui.Budget (USD)"), numeric: true },
      header: t("ui.Budget (USD)"),
      size: 130,
      enableSorting: false,
      cell: ({ row }) => (
        <MoneyCell value={row.original.max_budget} decimals={2} emptyText={t("ui.Unlimited")} showZero />
      ),
    },
    {
      id: "sso_user_id",
      accessorKey: "sso_user_id",
      meta: { title: t("ui.SSO ID") },
      header: () => (
        <span className="flex items-center gap-1.5">
          {t("ui.SSO ID")}
          <CellTooltip
            content={t(SSO_ID_HINT)}
            trigger={<Info className="size-3.5 shrink-0 text-muted-foreground" aria-label={t("ui.About SSO ID")} />}
          />
        </span>
      ),
      size: 160,
      enableSorting: false,
      cell: ({ row }) => (
        <span className="block max-w-40 truncate font-mono text-xs" title={row.original.sso_user_id ?? undefined}>
          {row.original.sso_user_id ?? "-"}
        </span>
      ),
    },
    {
      id: "key_count",
      accessorKey: "key_count",
      meta: { title: t("ui.Virtual Keys"), skeleton: "badge" },
      header: t("ui.Virtual Keys"),
      size: 120,
      enableSorting: false,
      cell: ({ row }) => {
        const keyCount = row.original.key_count;
        if (keyCount > 0) {
          return (
            <Badge
              variant="outline"
              className="whitespace-nowrap border-indigo-200 bg-indigo-50 font-normal text-indigo-600"
            >
              {keyCount} {keyCount === 1 ? t("ui.Key") : t("ui.Keys")}
            </Badge>
          );
        }
        return (
          <Badge variant="outline" className="whitespace-nowrap border-gray-200 bg-gray-50 font-normal text-gray-600">
            {t("ui.No Keys")}
          </Badge>
        );
      },
    },
    {
      id: "created_at",
      accessorKey: "created_at",
      meta: { title: t("ui.Created At") },
      header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Created At")} variant="header-cycle" />,
      size: 130,
      enableSorting: true,
      cell: ({ row }) => <DateCell value={row.original.created_at} precision="date" />,
    },
    {
      id: "updated_at",
      accessorKey: "updated_at",
      meta: { title: t("ui.Updated At") },
      header: t("ui.Updated At"),
      size: 130,
      enableSorting: false,
      cell: ({ row }) => <DateCell value={row.original.updated_at} precision="date" />,
    },
    {
      id: "actions",
      meta: { title: t("ui.Actions"), className: "text-right", headerClassName: "text-right" },
      header: () => <span className="sr-only">{t("ui.Actions")}</span>,
      size: 60,
      enableSorting: false,
      enableHiding: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <UserRowActions
            user={row.original}
            onUserClick={onUserClick}
            onDeleteUser={onDeleteUser}
            onResetPassword={onResetPassword}
          />
        </div>
      ),
    },
  ];

  if (!includeSelection) {
    return baseColumns;
  }

  return [
    createSelectionColumn<UserInfo>({
      rowAriaLabel: (row) => `Select ${row.original.user_email || row.original.user_id}`,
    }),
    ...baseColumns,
  ];
};
