/** 本文件提供账号批量选择、后台任务提交和逐账号执行结果，不在浏览器并发执行账号操作。 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";

import {
  listAccountPoolBatches,
  submitAccountPoolBatch,
  type BatchAction,
  type PolicyView,
} from "./AccountPoolManagementApi";
import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY } from "./useAccountPoolQuery";

const ACTIONS: readonly BatchAction[] = [
  "refresh",
  "authorize",
  "enable",
  "disable",
  "cooldown",
  "release",
  "policy",
  "delete",
];

const hasPendingBatch = (jobs: Awaited<ReturnType<typeof listAccountPoolBatches>> | undefined) =>
  jobs?.some((job) => job.items.some((item) => item.status === "queued" || item.status === "running")) ?? false;

export function AccountPoolBatchPanel({
  accessToken,
  environments,
  policies,
}: {
  accessToken: string;
  environments: AccountPoolEnvironment[];
  policies: PolicyView[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [action, setAction] = useState<BatchAction>("refresh");
  const [policyTemplateId, setPolicyTemplateId] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [authorizationItem, setAuthorizationItem] = useState<{
    accountId: string;
    authorization: NonNullable<
      Awaited<ReturnType<typeof listAccountPoolBatches>>[number]["items"][number]["authorization"]
    >;
  } | null>(null);
  const selectableEnvironments = useMemo(
    () => environments.filter((environment) => action === "delete" || environment.status !== "migration_required"),
    [action, environments],
  );
  const jobsQuery = {
    queryKey: ["account-pool", "batches", accessToken],
    queryFn: () => listAccountPoolBatches(accessToken),
    refetchInterval: (query: { state: { data?: Awaited<ReturnType<typeof listAccountPoolBatches>> } }) =>
      hasPendingBatch(query.state.data) ? 1000 : 10000,
    retry: false,
  };
  const jobs = useQuery(jobsQuery);
  const completedJobs = useMemo(
    () =>
      jobs.data?.filter(
        (job) =>
          job.items.length > 0 && job.items.every((item) => item.status === "succeeded" || item.status === "failed"),
      ) ?? [],
    [jobs.data],
  );
  const completedJobSignature = completedJobs
    .map((job) => `${job.job_id}:${job.items.map((item) => `${item.account_id}:${item.status}`).join(",")}`)
    .join("|");
  const hasCompletedPolicyChange = completedJobs.some((job) => job.action === "policy" || job.action === "delete");
  useEffect(() => {
    if (!completedJobSignature) return;
    void queryClient.invalidateQueries({ queryKey: ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY });
    if (hasCompletedPolicyChange) {
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "policies", accessToken] });
    }
  }, [accessToken, completedJobSignature, hasCompletedPolicyChange, queryClient]);
  const targets = useMemo(
    () =>
      environments
        .filter((environment) => selected.has(environment.id))
        .map((environment) => ({
          account_id: environment.id,
          version: environment.version,
          policy_version: policies.find((policy) => policy.card_id === environment.id)?.version ?? 0,
        })),
    [environments, policies, selected],
  );
  const selectedPolicy = policies.find((policy) => policy.card_id === policyTemplateId)?.policy ?? null;
  const mutation = useMutation({
    mutationFn: () => submitAccountPoolBatch(accessToken, action, targets, action === "policy" ? selectedPolicy : null),
    onSuccess: () => {
      toast.success(t("accountPool.batch.submitted"));
      setConfirmDeleteOpen(false);
      setSelected(new Set());
      void jobs.refetch();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const allSelected = selectableEnvironments.length > 0 && selected.size === selectableEnvironments.length;
  const policyTemplateRequired = action === "policy" && selectedPolicy === null;
  const submitDisabled = targets.length === 0 || mutation.isPending || policyTemplateRequired;
  const submit = () => {
    if (action === "delete") {
      setConfirmDeleteOpen(true);
      return;
    }
    mutation.mutate();
  };
  const toggle = (id: string, checked: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });

  return (
    <div className="grid gap-4 rounded-md border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium">{t("accountPool.batch.title")}</p>
          <p className="text-sm text-muted-foreground">{t("accountPool.batch.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={action} onValueChange={(value) => setAction(value as BatchAction)}>
            <SelectTrigger className="w-40" aria-label={t("accountPool.batch.action")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ACTIONS.map((item) => (
                <SelectItem key={item} value={item}>
                  {t(`accountPool.batch.actions.${item}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {action === "policy" && (
            <Select value={policyTemplateId} onValueChange={setPolicyTemplateId}>
              <SelectTrigger className="w-56" aria-label={t("accountPool.batch.policyTemplate")}>
                <SelectValue placeholder={t("accountPool.batch.selectPolicyTemplate")} />
              </SelectTrigger>
              <SelectContent>
                {policies.map((policy) => (
                  <SelectItem key={policy.card_id} value={policy.card_id} disabled={policy.policy === undefined}>
                    {t("accountPool.batch.policyTemplateOption", {
                      name:
                        environments.find((environment) => environment.id === policy.card_id)?.name ?? policy.card_id,
                      version: policy.version,
                    })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant={action === "delete" ? "destructive" : "default"} disabled={submitDisabled} onClick={submit}>
            {mutation.isPending
              ? t("accountPool.batch.submitting")
              : t("accountPool.batch.submit", { count: targets.length })}
          </Button>
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={allSelected}
          onCheckedChange={(checked) =>
            setSelected(
              checked === true ? new Set(selectableEnvironments.map((environment) => environment.id)) : new Set(),
            )
          }
        />
        {t("accountPool.batch.selectAll")}
      </label>
      <div className="grid max-h-52 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
        {selectableEnvironments.map((environment) => (
          <label key={environment.id} className="flex items-center gap-2 rounded border p-2 text-sm">
            <Checkbox
              checked={selected.has(environment.id)}
              onCheckedChange={(checked) => toggle(environment.id, checked === true)}
            />
            <span className="truncate">{environment.name}</span>
          </label>
        ))}
      </div>
      {jobs.isError && <p role="alert">{t("accountPool.batch.loadFailed")}</p>}
      {jobs.data?.slice(0, 5).map((job) => {
        const succeeded = job.items.filter((item) => item.status === "succeeded").length;
        const failed = job.items.filter((item) => item.status === "failed").length;
        const pending = job.items.length - succeeded - failed;
        return (
          <div key={job.job_id} className="rounded border p-3 text-sm">
            <div className="flex flex-wrap justify-between gap-2">
              <span>{t(`accountPool.batch.actions.${job.action}`)}</span>
              <span className="text-muted-foreground">
                {t("accountPool.batch.result", { succeeded, failed, pending })}
              </span>
            </div>
            {job.items.some((item) => item.status === "failed") && (
              <ul className="mt-2 grid gap-1 text-destructive">
                {job.items
                  .filter((item) => item.status === "failed")
                  .map((item) => (
                    <li key={item.account_id}>
                      {environments.find((environment) => environment.id === item.account_id)?.name ?? item.account_id}:{" "}
                      {item.message}
                    </li>
                  ))}
              </ul>
            )}
            {job.action === "authorize" && job.items.some((item) => item.authorization) && (
              <ul className="mt-2 grid gap-1">
                {job.items
                  .filter((item) => item.authorization)
                  .map((item) => (
                    <li key={item.account_id} className="flex items-center justify-between gap-2">
                      <span className="truncate">
                        {environments.find((environment) => environment.id === item.account_id)?.name ??
                          item.account_id}
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          if (item.authorization) {
                            setAuthorizationItem({ accountId: item.account_id, authorization: item.authorization });
                          }
                        }}
                      >
                        {t("accountPool.batch.viewAuthorization")}
                      </Button>
                    </li>
                  ))}
              </ul>
            )}
          </div>
        );
      })}
      <Dialog open={authorizationItem !== null} onOpenChange={(open) => !open && setAuthorizationItem(null)}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>{t("accountPool.batch.authorizationTitle")}</DialogTitle>
            <DialogDescription>
              {t("accountPool.batch.authorizationDescription", {
                name:
                  environments.find((environment) => environment.id === authorizationItem?.accountId)?.name ??
                  authorizationItem?.accountId,
              })}
            </DialogDescription>
          </DialogHeader>
          {authorizationItem && (
            <AccountPoolAuthorizationPanel
              authorization={authorizationItem.authorization}
              idPrefix={`account-pool-batch-${authorizationItem.accountId}`}
            />
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={confirmDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setConfirmDeleteOpen(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("accountPool.batch.confirmDeleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("accountPool.batch.confirmDeleteDescription", { count: targets.length })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>{t("accountPool.cancel")}</AlertDialogCancel>
            <Button variant="destructive" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
              {mutation.isPending ? t("accountPool.batch.submitting") : t("accountPool.batch.confirmDelete")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
