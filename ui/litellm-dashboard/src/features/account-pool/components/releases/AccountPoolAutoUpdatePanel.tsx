"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/http/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { pageHref } from "@/utils/migratedPages";
import {
  checkAutoUpdate,
  getAutoUpdate,
  saveAutoUpdate,
  type AutoUpdateSettings,
} from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

type Draft = { enabled: boolean; interval: string; revision: number };

export function AccountPoolAutoUpdatePanel({ accessToken }: { accessToken: string }) {
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "accountPool.autoUpdate" });
  const client = useQueryClient();
  const queryKey = accountPoolQueryKeys.autoUpdate(accessToken);
  const queryOptions = { queryKey, queryFn: () => getAutoUpdate(accessToken), refetchInterval: 15000, retry: false };
  const query = useQuery(queryOptions);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const save = useMutation({
    mutationFn: (body: AutoUpdateSettings) => saveAutoUpdate(accessToken, body),
    onMutate: () => client.cancelQueries({ queryKey }),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
      setAcknowledged(false);
    },
  });
  const check = useMutation({
    mutationFn: () => checkAutoUpdate(accessToken),
    onMutate: () => client.cancelQueries({ queryKey }),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      void client.invalidateQueries({ queryKey: accountPoolQueryKeys.releases(accessToken) });
    },
  });
  const reload = useMutation({
    mutationFn: () => getAutoUpdate(accessToken),
    onSuccess: (data) => {
      client.setQueryData(queryKey, data);
      setDraft(null);
      setAcknowledged(false);
      save.reset();
      check.reset();
    },
  });
  const data = query.data;
  const checkedTime = (value: number | null | undefined) =>
    value == null ? t("none") : new Date(value * 1000).toLocaleString(i18n.language);
  const busy = save.isPending || check.isPending || reload.isPending;
  const form = draft ?? {
    enabled: data?.enabled ?? false,
    interval: String(data?.interval_minutes ?? 5),
    revision: data?.revision ?? 0,
  };
  const interval = Number(form.interval);
  const validInterval = Number.isInteger(interval) && interval >= 5 && interval <= 1440;
  const enableAuthorized = acknowledged && data?.database_backups_enabled;
  const scheduleAuthorized = !form.enabled || enableAuthorized;
  const canSave = draft && validInterval && scheduleAuthorized;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const error = save.error ?? check.error ?? reload.error;
  const edit = (values: Partial<Draft>) => {
    setDraft({ ...form, ...values });
    save.reset();
    check.reset();
  };
  const submit = () => {
    check.reset();
    const settings: AutoUpdateSettings = {
      enabled: form.enabled,
      interval_minutes: interval,
      revision: form.revision,
      acknowledge_downtime: form.enabled && acknowledged,
    };
    save.mutate(settings);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isPending && <p role="status">{t("loading")}</p>}
        {query.isError && (
          <p role="alert" className="text-sm text-destructive">
            {query.error instanceof ApiError && query.error.status === 404
              ? t("missingApi")
              : t("loadError", { message: query.error.message })}
          </p>
        )}
        {data && (
          <>
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="auto-update-enabled">{t("enable")}</Label>
              <Switch
                id="auto-update-enabled"
                checked={form.enabled}
                disabled={busy || (!form.enabled && !data.database_backups_enabled)}
                onCheckedChange={(enabled) => {
                  edit({ enabled });
                  setAcknowledged(false);
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="auto-update-interval">{t("interval")}</Label>
              <Input
                id="auto-update-interval"
                type="number"
                min={5}
                max={1440}
                step={1}
                value={form.interval}
                disabled={busy}
                aria-invalid={!validInterval}
                onChange={(event) => edit({ interval: event.target.value })}
              />
              {!validInterval && (
                <p role="alert" className="text-sm text-destructive">
                  {t("invalidInterval")}
                </p>
              )}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              {t("downtime")}
            </p>
            {!data.database_backups_enabled && (
              <p className="text-sm text-muted-foreground">{t("backupRequired")}</p>
            )}
            {form.enabled && (
              <div className="flex items-start gap-2">
                <Checkbox
                  id="auto-update-acknowledgement"
                  checked={acknowledged}
                  onCheckedChange={setAcknowledged}
                  disabled={busy}
                />
                <Label htmlFor="auto-update-acknowledgement" className="text-sm leading-5">
                  {t("acknowledge")}
                </Label>
              </div>
            )}
            <dl className="space-y-2 text-xs">
              <div>
                <dt className="text-muted-foreground">{t("currentStatus")}</dt>
                <dd>{t(`status.${data.status}`)}</dd>
              </div>
              {data.message && (
                <div>
                  <dt className="sr-only">{t("statusDetails")}</dt>
                  <dd>{data.message}</dd>
                </div>
              )}
              <div>
                <dt className="text-muted-foreground">{t("lastCheck")}</dt>
                <dd>{checkedTime(data.last_checked_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("nextCheck")}</dt>
                <dd>{checkedTime(data.next_check_at)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("commits")}</dt>
                <dd className="break-all">
                  {data.current_commit ?? t("unknown")} / {data.candidate_commit ?? t("none")}
                </dd>
              </div>
              {data.last_job_id && (
                <div>
                  <dt className="text-muted-foreground">{t("lastJob")}</dt>
                  <dd className="break-all">{data.last_job_id}</dd>
                </div>
              )}
            </dl>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !canSave} onClick={submit}>
                {save.isPending ? t("saving") : t("save")}
              </Button>
              <Button
                variant="outline"
                disabled={busy || ["checking", "queued", "deploying"].includes(data.status)}
                onClick={() => {
                  save.reset();
                  check.mutate();
                }}
              >
                {check.isPending ? t("submitting") : t("check")}
              </Button>
            </div>
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {conflict ? t("conflict", { message: error.message }) : error.message}
          </p>
        )}
        {save.isSuccess && (
          <p role="status" className="text-sm">
            {t("saved")}
          </p>
        )}
        {check.isSuccess && (
          <p role="status" className="text-sm">
            {t("submitted")}
          </p>
        )}
        {(conflict || query.isError) && (
          <Button variant="outline" disabled={busy} onClick={() => reload.mutate()}>
            {t("reload")}
          </Button>
        )}
        <Link
          className="block text-sm text-primary hover:underline"
          href={`${pageHref("account-pool")}?tab=releases`}
        >
          {t("logs")}
        </Link>
      </CardContent>
    </Card>
  );
}
