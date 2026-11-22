/** 本文件提供账号批量选择、后台任务提交和逐账号执行结果，不在浏览器并发执行账号操作。 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
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
} from "../../api/AccountPoolManagementApi";
import { AccountPoolAuthorizationPanel } from "../credentials/AccountPoolAuthorizationPanel";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY } from "../../hooks/useAccountPoolQuery";

const MAX_BATCH_TARGETS = 100;

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
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<BatchAction>("refresh");
  const [policyTemplateId, setPolicyTemplateId] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [authorizationItem, setAuthorizationItem] = useState<{
    accountId: string;
    authorization: NonNullable<
      Awaited<ReturnType<typeof listAccountPoolBatches>>[number]["items"][number]["authorization"]
    >;
  } | null>(null);
  const environmentsById = useMemo(
    () => new Map(environments.map((environment) => [environment.id, environment] as const)),
    [environments],
  );
  const policiesByCard = useMemo(
    () => new Map(policies.map((policy) => [policy.card_id, policy] as const)),
    [policies],
  );
  const selectableEnvironments = useMemo(() => {
    const trimmed = search.trim().toLocaleLowerCase();
    if (!trimmed) return environments;
    return environments.filter(
      (environment) =>
        environment.name.toLocaleLowerCase().includes(trimmed) ||
        environment.id.toLocaleLowerCase().includes(trimmed),
    );
  }, [environments, search]);
  const jobsQuery = {
    queryKey: accountPoolQueryKeys.batches(accessToken),
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
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.policies(accessToken) });
    }
  }, [accessToken, completedJobSignature, hasCompletedPolicyChange, queryClient]);
  const validSelected = useMemo(
    () => new Set([...selected].filter((id) => environmentsById.has(id))),
    [environmentsById, selected],
  );
  const targets = useMemo(
    () =>
      environments
        .filter((environment) => validSelected.has(environment.id))
        .map((environment) => ({
          account_id: environment.id,
          version: environment.version,
          policy_version: policiesByCard.get(environment.id)?.version ?? 0,
        })),
    [environments, policiesByCard, validSelected],
  );
  const selectedPolicy = policyTemplateId ? (policiesByCard.get(policyTemplateId)?.policy ?? null) : null;
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
  const overTargetLimit = targets.length > MAX_BATCH_TARGETS;
  const selectableCap = selectableEnvironments.slice(0, MAX_BATCH_TARGETS);
  const allSelected = selectableCap.length > 0 && selectableCap.every((env) => validSelected.has(env.id));
  const policyTemplateRequired = action === "policy" && selectedPolicy === null;
  const submitDisabled = targets.length === 0 || overTargetLimit || mutation.isPending || policyTemplateRequired;
  const submit = () => {
    if (action === "delete") {
      setConfirmDeleteOpen(true);
      return;
    }
    mutation.mutate();
  };
  const toggle = (id: string, checked: boolean) => {
    if (checked && !validSelected.has(id) && validSelected.size >= MAX_BATCH_TARGETS) {
      toast.error(`单次批量操作最多选择 ${MAX_BATCH_TARGETS} 张卡片`);
      return;
    }
    setSelected((current) => {
      const next = new Set([...current].filter((item) => environmentsById.has(item)));
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

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
            <Select
              items={policies.map((policy) => ({
                value: policy.card_id,
                label: t("accountPool.batch.policyTemplateOption", {
                  name:
                    environmentsById.get(policy.card_id)?.name ??
                    `已删除卡片 (${policy.card_id.slice(0, 8)})`,
                  version: policy.version,
                }),
              }))}
              value={policyTemplateId}
              onValueChange={setPolicyTemplateId}
            >
              <SelectTrigger className="w-56" aria-label={t("accountPool.batch.policyTemplate")}>
                <SelectValue placeholder={t("accountPool.batch.selectPolicyTemplate")} />
              </SelectTrigger>
              <SelectContent>
                {policies.map((policy) => (
                  <SelectItem key={policy.card_id} value={policy.card_id} disabled={policy.policy === undefined}>
                    {t("accountPool.batch.policyTemplateOption", {
                      name: environmentsById.get(policy.card_id)?.name ?? policy.card_id,
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
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={allSelected}
            onCheckedChange={(checked) =>
              setSelected(
                checked === true ? new Set(selectableCap.map((environment) => environment.id)) : new Set(),
              )
            }
          />
          {t("accountPool.batch.selectAll")}
          {selectableEnvironments.length > MAX_BATCH_TARGETS && (
            <span className="text-xs text-muted-foreground">（全选上限 {MAX_BATCH_TARGETS}）</span>
          )}
        </label>
        <Input
          aria-label="筛选批量卡片"
          placeholder="按卡片名称或 ID 筛选"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="h-8 w-full sm:w-64"
        />
      </div>
      {overTargetLimit && (
        <p role="alert" className="text-xs text-destructive">
          单次批量操作最多选择 {MAX_BATCH_TARGETS} 张卡片，当前已选 {targets.length} 张。
        </p>
      )}
      <div className="grid max-h-52 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
        {selectableEnvironments.map((environment) => (
          <label key={environment.id} className="flex items-center gap-2 rounded border p-2 text-sm">
            <Checkbox
              checked={validSelected.has(environment.id)}
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
                      {environmentsById.get(item.account_id)?.name ?? item.account_id}: {item.message}
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
                        {environmentsById.get(item.account_id)?.name ?? item.account_id}
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
                  (authorizationItem ? environmentsById.get(authorizationItem.accountId)?.name : undefined) ??
                  authorizationItem?.accountId,
              })}
            </DialogDescription>
          </DialogHeader>
          {authorizationItem && (
            <AccountPoolAuthorizationPanel
              authorization={authorizationItem.authorization}
              idPrefix={`account-pool-batch-${authorizationItem.accountId}`}
              accessToken={accessToken}
              environmentId={authorizationItem.accountId}
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
