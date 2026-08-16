"use client";

import { InfoCircleOutlined } from "@ant-design/icons";
import { ColumnDef } from "@tanstack/react-table";
import { Popover, Typography } from "antd";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { DataTableMultiSortHeader, DataTableSortHeader, type DataTableSortField } from "@/components/shared/DataTable";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DateCell,
  IdCell,
  IdentityCell,
  ModelsCell,
  SpendBudgetCell,
  StatusBadge,
  type StatusTone,
} from "@/components/shared/table_cells";

import DefaultProxyAdminTag from "../common_components/DefaultProxyAdminTag";
import { KeyResponse, Team } from "../key_team_helpers/key_list";
import { Organization } from "../networking";

interface KeyStatus {
  tone: StatusTone;
  label: string;
  tooltip?: string;
}

const SPEND_BUDGET_SORT_FIELDS = (t: TFunction): DataTableSortField[] => [
  { id: "spend", label: t("ui.Spend") },
  { id: "max_budget", label: t("ui.Budget") },
];

const getKeyStatus = (key: KeyResponse, t: TFunction): KeyStatus => {
  if (key.blocked === true) {
    const isScimBlocked = (key.metadata as Record<string, unknown> | null | undefined)?.scim_blocked === true;
    return {
      tone: "error",
      label: t("ui.Blocked"),
      tooltip: isScimBlocked
        ? t("ui.Blocked by SCIM (external identity provider deactivated or deleted the owning user).")
        : t("ui.Blocked. Requests using this key will be rejected with 401."),
    };
  }
  const expiresAt = key.expires ? Date.parse(key.expires) : Number.NaN;
  if (!Number.isNaN(expiresAt) && expiresAt < Date.now()) {
    return { tone: "warning", label: t("ui.Expired"), tooltip: t("ui.This key has passed its expiry date.") };
  }
  return {
    tone: "success",
    label: t("ui.Active"),
    tooltip: t("ui.This key is not blocked and has not expired."),
  };
};

const UserPopoverCell = ({
  userAlias,
  userEmail,
  userId,
  width,
}: {
  userAlias: string | null;
  userEmail: string | null;
  userId: string | null;
  width: number;
}) => {
  const { t } = useTranslation();
  const displayValue = userAlias || userEmail || userId;
  const isDefaultAdmin = userId === "default_user_id";

  const popoverContent = (
    <div className="flex flex-col gap-2 text-xs min-w-[200px] max-w-[300px]">
      {[
        { label: t("ui.User Alias"), value: userAlias },
        { label: t("ui.User Email"), value: userEmail },
        { label: t("ui.User ID"), value: userId },
      ].map(({ label, value }) => (
        <div key={label} className="flex flex-col min-w-0">
          <span className="text-gray-400">{label}</span>
          {value ? (
            <Typography.Text className="font-mono text-xs" ellipsis={{ tooltip: value }} copyable>
              {value}
            </Typography.Text>
          ) : (
            <span className="font-mono">-</span>
          )}
        </div>
      ))}
    </div>
  );

  if (isDefaultAdmin && !userAlias && !userEmail) {
    return (
      <Popover content={popoverContent} trigger="hover" placement="bottomLeft">
        <span className="cursor-default">
          <DefaultProxyAdminTag userId={userId} />
        </span>
      </Popover>
    );
  }

  return (
    <Popover content={popoverContent} trigger="hover" placement="bottomLeft">
      <span className="font-mono text-xs truncate block cursor-default" style={{ maxWidth: width, overflow: "hidden" }}>
        {displayValue || "-"}
      </span>
    </Popover>
  );
};

const InfoHeader = ({ label, tooltip }: { label: string; tooltip: string }) => {
  const { t } = useTranslation();
  return (
    <span className="flex items-center gap-1">
      {t(label)}
      <Popover content={t(tooltip)} trigger="hover">
        <InfoCircleOutlined className="text-gray-400 text-xs cursor-help" />
      </Popover>
    </span>
  );
};

interface KeyTableColumnsDeps {
  allTeams: Team[];
  organizations: Organization[];
  onSelectKey: (key: KeyResponse) => void;
  t: TFunction;
}

export const getKeyTableColumns = ({
  allTeams,
  organizations,
  onSelectKey,
  t,
}: KeyTableColumnsDeps): ColumnDef<KeyResponse>[] => [
  {
    id: "key_alias",
    accessorKey: "key_alias",
    meta: {
      title: t("ui.Key"),
      renderSkeleton: () => (
        <div className="flex flex-col gap-1 py-1">
          <Skeleton className="h-4 w-32" />
          <div className="flex items-center gap-2">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        </div>
      ),
    },
    header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Key")} variant="header-cycle" />,
    size: 260,
    enableSorting: true,
    cell: ({ row }) => {
      const status = getKeyStatus(row.original, t);
      return (
        <IdentityCell
          title={row.original.key_alias || "-"}
          subtitle={row.original.key_name}
          badge={
            <StatusBadge
              tone={status.tone}
              label={status.label}
              tooltip={status.tooltip}
              dataTestId={`key-status-${row.original.token_id}`}
            />
          }
          onClick={() => onSelectKey(row.original)}
        />
      );
    },
  },
  {
    id: "token",
    accessorKey: "token",
    meta: { title: t("ui.Key ID") },
    header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Key ID")} variant="header-cycle" />,
    size: 120,
    enableSorting: true,
    cell: (info) => <IdCell value={info.getValue() as string | null} onClick={() => onSelectKey(info.row.original)} />,
  },
  {
    id: "team_alias",
    accessorKey: "team_id",
    meta: { title: t("ui.Team") },
    header: t("ui.Team"),
    size: 120,
    enableSorting: false,
    cell: (info) => {
      const teamId = info.getValue() as string | null;
      if (!teamId) return "-";
      const team = allTeams.find((t) => t.team_id === teamId);
      const displayValue = team?.team_alias || teamId;
      const width = info.cell.column.getSize();
      return (
        <span className="font-mono text-xs truncate block" style={{ maxWidth: width, overflow: "hidden" }}>
          {displayValue}
        </span>
      );
    },
  },
  {
    id: "organization_alias",
    accessorKey: "org_id",
    meta: { title: t("ui.Organization") },
    header: t("ui.Organization"),
    size: 140,
    enableSorting: false,
    cell: (info) => {
      const orgId = info.getValue() as string | null;
      if (!orgId) return "-";
      const org = organizations.find((o) => o.organization_id === orgId);
      const displayValue = org?.organization_alias || orgId;
      const width = info.cell.column.getSize();
      return (
        <span className="font-mono text-xs truncate block" style={{ maxWidth: width, overflow: "hidden" }}>
          {displayValue}
        </span>
      );
    },
  },
  {
    id: "user",
    accessorKey: "user",
    meta: { title: t("ui.User") },
    header: () => (
      <InfoHeader label="User" tooltip="Displays the first available value: User Alias, User Email, or User ID." />
    ),
    size: 160,
    enableSorting: false,
    cell: ({ row }) => {
      const key = row.original;
      return (
        <UserPopoverCell
          userAlias={key.user?.user_alias ?? null}
          userEmail={key.user?.user_email ?? key.user_email ?? null}
          userId={key.user_id ?? null}
          width={160}
        />
      );
    },
  },
  {
    id: "created_at",
    accessorKey: "created_at",
    meta: { title: t("ui.Created At") },
    header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Created At")} variant="header-cycle" />,
    size: 120,
    enableSorting: true,
    cell: (info) => <DateCell value={info.getValue() as string | null} precision="date" />,
  },
  {
    id: "created_by",
    accessorKey: "created_by",
    meta: { title: t("ui.Created By") },
    header: t("ui.Created By"),
    size: 160,
    enableSorting: false,
    cell: (info) => {
      const userId = info.getValue() as string | null;
      if (!userId) return "-";
      const createdByUser = info.row.original.created_by_user;
      return (
        <UserPopoverCell
          userAlias={createdByUser?.user_alias ?? null}
          userEmail={createdByUser?.user_email ?? null}
          userId={userId}
          width={160}
        />
      );
    },
  },
  {
    id: "updated_at",
    accessorKey: "updated_at",
    meta: { title: t("ui.Updated At") },
    header: ({ column }) => <DataTableSortHeader column={column} title={t("ui.Updated At")} variant="header-cycle" />,
    size: 120,
    enableSorting: true,
    cell: (info) => <DateCell value={info.getValue() as string | null} precision="date" fallback={t("ui.Never")} />,
  },
  {
    id: "last_active",
    accessorKey: "last_active",
    meta: { title: t("ui.Last Active") },
    header: () => (
      <InfoHeader
        label="Last Active"
        tooltip="This is a new field and is not backfilled. Only new key usage will update this value."
      />
    ),
    size: 130,
    enableSorting: false,
    cell: (info) => <DateCell value={info.getValue() as string | null} precision="date" fallback={t("ui.Unknown")} />,
  },
  {
    id: "expires",
    accessorKey: "expires",
    meta: { title: t("ui.Expires") },
    header: t("ui.Expires"),
    size: 120,
    enableSorting: false,
    cell: (info) => <DateCell value={info.getValue() as string | null} precision="date" fallback={t("ui.Never")} />,
  },
  {
    id: "spend",
    accessorKey: "spend",
    meta: { title: t("ui.Spend / Budget"), skeleton: "meter" },
    header: ({ table }) => <DataTableMultiSortHeader table={table} fields={SPEND_BUDGET_SORT_FIELDS(t)} />,
    size: 180,
    enableSorting: true,
    cell: ({ row }) => {
      const teamId = row.original.team_id;
      const team = allTeams.find((t) => t.team_id === teamId);
      return (
        <SpendBudgetCell
          spend={row.original.spend}
          maxBudget={row.original.max_budget}
          teamMaxBudget={team?.max_budget ?? null}
        />
      );
    },
  },
  {
    id: "budget_reset_at",
    accessorKey: "budget_reset_at",
    meta: { title: t("ui.Budget Reset") },
    header: t("ui.Budget Reset"),
    size: 130,
    enableSorting: false,
    cell: (info) => <DateCell value={info.getValue() as string | null} fallback={t("ui.Never")} />,
  },
  {
    id: "models",
    accessorKey: "models",
    meta: { title: t("ui.Models"), skeleton: "chips" },
    header: t("ui.Models"),
    size: 220,
    enableSorting: false,
    cell: (info) => (
      <ModelsCell
        models={info.getValue() as string[] | null | undefined}
        allowedRoutes={info.row.original.allowed_routes}
        keyType={info.row.original.key_type}
      />
    ),
  },
  {
    id: "rate_limits",
    meta: { title: t("ui.Rate Limits") },
    header: t("ui.Rate Limits"),
    size: 140,
    enableSorting: false,
    cell: ({ row }) => {
      const key = row.original;
      return (
        <div className="text-xs">
          <div>{t("ui.TPM")}: {key.tpm_limit !== null ? key.tpm_limit : t("ui.Unlimited")}</div>
          <div>{t("ui.RPM")}: {key.rpm_limit !== null ? key.rpm_limit : t("ui.Unlimited")}</div>
        </div>
      );
    },
  },
];

export const KEY_TABLE_HIDDEN_COLUMNS: Record<string, boolean> = {
  token: false,
  organization_alias: false,
  created_by: false,
  updated_at: false,
  expires: false,
  rate_limits: false,
};
