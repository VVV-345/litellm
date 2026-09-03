/** 本文件提供号池管理首页，编排数据查询、卡片操作、创建授权和配置弹窗。 */

"use client";

import { Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { AdminOnlyNotice } from "@/components/shared/AdminOnlyNotice";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import { AccountPoolCard } from "./AccountPoolCard";
import { AccountPoolConfigDialog } from "./AccountPoolConfigDialog";
import { AccountPoolCreateDialog } from "./AccountPoolCreateDialog";
import { canManageAccountPool } from "./AccountPoolPermissions";
import type { AccountPoolAuthorization, AccountPoolEnvironment, AccountPoolStatus } from "./AccountPoolTypes";
import { filterAccountPoolEnvironments, paginateAccountPoolEnvironments } from "./accountPoolSelectors";
import { useAccountPoolMutations } from "./useAccountPoolMutations";
import { useAccountPoolQuery } from "./useAccountPoolQuery";

const PAGE_SIZE = 24;
const STATUS_FILTERS: ReadonlyArray<"all" | AccountPoolStatus> = [
  "all",
  "provisioning",
  "awaiting_authorization",
  "validating",
  "ready",
  "cooling_down",
  "disabled",
  "error",
  "deleting",
];

export default function AccountPoolPage() {
  const { t } = useTranslation();
  const { accessToken, userRole, isViewOnly } = useAuthorized();
  const [createOpen, setCreateOpen] = useState(false);
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(null);
  const [configEnvironment, setConfigEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [deleteEnvironment, setDeleteEnvironment] = useState<AccountPoolEnvironment | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | AccountPoolStatus>("all");
  const [page, setPage] = useState(1);
  const canManage = canManageAccountPool(userRole, isViewOnly);
  const environmentsQuery = useAccountPoolQuery(accessToken, canManage);
  const { updateMutation, authorizeMutation, deleteMutation } = useAccountPoolMutations(
    accessToken,
    canManage,
    (result) => {
      setAuthorization(result);
      setCreateOpen(true);
    },
    () => setDeleteEnvironment(null),
  );
  const environments = environmentsQuery.data ?? [];
  const filteredEnvironments = useMemo(
    () => filterAccountPoolEnvironments(environments, search, statusFilter),
    [environments, search, statusFilter],
  );
  const pageCount = Math.max(1, Math.ceil(filteredEnvironments.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleEnvironments = paginateAccountPoolEnvironments(filteredEnvironments, currentPage, PAGE_SIZE);
  const readyCount = environments.filter((environment) => environment.status === "ready" && environment.enabled).length;
  const awaitingCount = environments.filter((environment) => environment.status === "awaiting_authorization").length;
  const busy = updateMutation.isPending || deleteMutation.isPending || authorizeMutation.isPending;

  if (!canManage) return <AdminOnlyNotice pageTitle={t("accountPool.title")} />;

  const openCreateDialog = () => {
    setAuthorization(null);
    setCreateOpen(true);
  };

  const renderContent = () => {
    if (environmentsQuery.isLoading) {
      return (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {["one", "two", "three"].map((key) => (
            <Skeleton key={key} className="h-64 w-full" />
          ))}
        </div>
      );
    }
    if (environmentsQuery.isError) {
      const managerNotConfigured = environmentsQuery.error.message === "Account Pool Manager is not configured";
      return (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-6">
          <p className="font-medium text-destructive">
            {managerNotConfigured ? t("accountPool.managerNotConfigured") : t("accountPool.loadFailed")}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {managerNotConfigured ? t("accountPool.managerNotConfiguredDescription") : environmentsQuery.error.message}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => void environmentsQuery.refetch()}
          >
            {t("accountPool.retry")}
          </Button>
        </div>
      );
    }
    if (environments.length === 0) {
      return (
        <div className="rounded-md border border-dashed border-border p-12 text-center">
          <p className="font-medium">{t("accountPool.noEnvironments")}</p>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.noEnvironmentsDescription")}</p>
          <Button type="button" className="mt-4" onClick={openCreateDialog}>
            <Plus />
            {t("accountPool.createEnvironment")}
          </Button>
        </div>
      );
    }
    return (
      <>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleEnvironments.map((environment) => (
            <AccountPoolCard
              key={environment.id}
              environment={environment}
              onConfigure={setConfigEnvironment}
              onEnabledChange={(current, enabled) => updateMutation.mutate({ environment: current, enabled })}
              onAuthorize={(current) => authorizeMutation.mutate(current)}
              onDelete={setDeleteEnvironment}
              disabled={busy}
            />
          ))}
        </div>
        {filteredEnvironments.length === 0 && (
          <div className="rounded-md border border-dashed border-border p-12 text-center">
            <p className="font-medium">{t("accountPool.noMatchingEnvironments")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.noMatchingEnvironmentsDescription")}</p>
          </div>
        )}
        {pageCount > 1 && (
          <div className="mt-4 flex items-center justify-between gap-3" aria-label={t("accountPool.pagination")}>
            <p className="text-sm text-muted-foreground">
              {t("accountPool.showing", {
                from: (currentPage - 1) * PAGE_SIZE + 1,
                to: Math.min(currentPage * PAGE_SIZE, filteredEnvironments.length),
                count: filteredEnvironments.length,
              })}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={currentPage === 1}
              >
                {t("accountPool.previousPage")}
              </Button>
              <span className="text-sm text-muted-foreground">
                {t("accountPool.page", { current: currentPage, total: pageCount })}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                disabled={currentPage === pageCount}
              >
                {t("accountPool.nextPage")}
              </Button>
            </div>
          </div>
        )}
      </>
    );
  };

  return (
    <div className="w-full p-6">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">{t("accountPool.title")}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.description")}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge variant="outline">{t("accountPool.totalEnvironments", { count: environments.length })}</Badge>
              <Badge variant="outline">{t("accountPool.readyEnvironments", { count: readyCount })}</Badge>
              {awaitingCount > 0 && (
                <Badge variant="secondary">
                  {t("accountPool.awaitingAuthorizationEnvironments", { count: awaitingCount })}
                </Badge>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => void environmentsQuery.refetch()}
              disabled={environmentsQuery.isFetching}
              aria-label={t("accountPool.refresh")}
              title={t("accountPool.refresh")}
            >
              <RefreshCw className={environmentsQuery.isFetching ? "animate-spin" : undefined} />
            </Button>
            <Button type="button" onClick={openCreateDialog} disabled={busy}>
              <Plus />
              {t("accountPool.createEnvironment")}
            </Button>
          </div>
        </div>

        {!environmentsQuery.isLoading && !environmentsQuery.isError && environments.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_13rem]">
            <Input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
              placeholder={t("accountPool.search")}
              aria-label={t("accountPool.search")}
            />
            <Select
              value={statusFilter}
              onValueChange={(value) => {
                if (STATUS_FILTERS.some((filter) => filter === value)) {
                  setStatusFilter(value as "all" | AccountPoolStatus);
                  setPage(1);
                }
              }}
            >
              <SelectTrigger aria-label={t("accountPool.filterByStatus")} className="w-full">
                <SelectValue placeholder={t("accountPool.status.all")} />
              </SelectTrigger>
              <SelectContent>
                {STATUS_FILTERS.map((filter) => (
                  <SelectItem key={filter} value={filter}>
                    {t(`accountPool.status.${filter}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {renderContent()}
      </div>
      {createOpen && (
        <AccountPoolCreateDialog
          key={authorization?.environment.id ?? "create"}
          accessToken={accessToken}
          initialAuthorization={authorization}
          open
          onOpenChange={(open) => {
            setCreateOpen(open);
            if (!open) setAuthorization(null);
          }}
          onCreated={() => void environmentsQuery.refetch()}
        />
      )}
      {configEnvironment && (
        <AccountPoolConfigDialog
          key={configEnvironment.id}
          accessToken={accessToken}
          environment={configEnvironment}
          open
          onOpenChange={(open) => !open && setConfigEnvironment(null)}
          onRefresh={() => void environmentsQuery.refetch()}
          onSaved={() => setConfigEnvironment(null)}
        />
      )}
      <AlertDialog
        open={deleteEnvironment !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteEnvironment(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("accountPool.confirmDeleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteEnvironment
                ? t("accountPool.confirmDeleteDescription", { name: deleteEnvironment.name })
                : t("accountPool.confirmDeleteUnavailable")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>{t("accountPool.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending || deleteEnvironment === null}
              onClick={() => {
                if (deleteEnvironment) deleteMutation.mutate(deleteEnvironment);
              }}
            >
              {deleteMutation.isPending ? t("accountPool.deleting") : t("accountPool.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
