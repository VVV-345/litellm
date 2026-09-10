/** 本文件提供账号批量选择、后台任务提交和逐账号执行结果，不在浏览器并发执行账号操作。 */

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";

import { listAccountPoolBatches, submitAccountPoolBatch, type BatchAction } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY } from "./useAccountPoolQuery";

const ACTIONS: readonly BatchAction[] = ["refresh", "enable", "disable", "cooldown", "release"];

const hasPendingBatch = (jobs: Awaited<ReturnType<typeof listAccountPoolBatches>> | undefined) =>
  jobs?.some((job) => job.items.some((item) => item.status === "queued" || item.status === "running")) ?? false;

export function AccountPoolBatchPanel({
  accessToken,
  environments,
}: {
  accessToken: string;
  environments: AccountPoolEnvironment[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [action, setAction] = useState<BatchAction>("refresh");
  const jobsQuery = {
    queryKey: ["account-pool", "batches", accessToken],
    queryFn: () => listAccountPoolBatches(accessToken),
    refetchInterval: (query: { state: { data?: Awaited<ReturnType<typeof listAccountPoolBatches>> } }) =>
      hasPendingBatch(query.state.data) ? 1000 : 10000,
    retry: false,
  };
  const jobs = useQuery(jobsQuery);
  const targets = useMemo(
    () =>
      environments
        .filter((environment) => selected.has(environment.id))
        .map((environment) => ({
          account_id: environment.id,
          version: environment.version,
          policy_version: 0,
        })),
    [environments, selected],
  );
  const mutation = useMutation({
    mutationFn: () => submitAccountPoolBatch(accessToken, action, targets),
    onSuccess: () => {
      toast.success(t("accountPool.batch.submitted"));
      setSelected(new Set());
      void jobs.refetch();
      void queryClient.invalidateQueries({ queryKey: ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const allSelected = environments.length > 0 && selected.size === environments.length;
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
          <Button disabled={targets.length === 0 || mutation.isPending} onClick={() => mutation.mutate()}>
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
            setSelected(checked === true ? new Set(environments.map((environment) => environment.id)) : new Set())
          }
        />
        {t("accountPool.batch.selectAll")}
      </label>
      <div className="grid max-h-52 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
        {environments.map((environment) => (
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
          </div>
        );
      })}
    </div>
  );
}
