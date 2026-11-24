/** 展示脱敏浏览器事件，提供会话/卡片筛选及明确确认的按天清理。 */
"use client";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { deleteManagerLogs, getManagerLogs } from "../../api/AccountPoolManagementApi";
import type { ManagerLogFilter, ManagerLogItem } from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { formatDateTime } from "../../utils/AccountPoolFormatters";

const PAGE_SIZE = 50;
const EMPTY_FILTER = { limit: PAGE_SIZE, offset: 0 };
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const severityBadges = { info: "outline", warning: "secondary", error: "destructive" } as const;

function LogRows({ items }: { items: ManagerLogItem[] }) {
  const { t, i18n } = useTranslation();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("accountPool.managerLogs.tableTime")}</TableHead>
          <TableHead>{t("accountPool.managerLogs.tableSeverity")}</TableHead>
          <TableHead>{t("accountPool.managerLogs.tableStepState")}</TableHead>
          <TableHead>{t("accountPool.managerLogs.tableReason")}</TableHead>
          <TableHead>{t("accountPool.managerLogs.tableSessionId")}</TableHead>
          <TableHead>{t("accountPool.managerLogs.tableCardId")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.event_id}>
            <TableCell className="whitespace-nowrap tabular-nums">
              {formatDateTime(item.occurred_at, i18n.language)}
            </TableCell>
            <TableCell>
              <Badge variant={severityBadges[item.severity]}>
                {t(`accountPool.managerLogs.severity.${item.severity}`)}
              </Badge>
            </TableCell>
            <TableCell>
              {t(`accountPool.managerLogs.steps.${item.step}`, {
                defaultValue: t("accountPool.managerLogs.unknownStep"),
              })}{" "}
              <span className="text-muted-foreground">
                · {t(`accountPool.managerLogs.states.${item.state}`)}
              </span>
            </TableCell>
            <TableCell className="min-w-40 whitespace-normal">
              {item.reason
                ? t(`accountPool.managerLogs.reasons.${item.reason}`, {
                    defaultValue: t("accountPool.managerLogs.unclassifiedReason"),
                  })
                : t("accountPool.managerLogs.noReason")}
              {item.http_status ? ` · HTTP ${item.http_status}` : ""}
              {item.duration_ms == null ? "" : ` · ${item.duration_ms} ms`}
            </TableCell>
            <TableCell className="font-mono text-xs">
              {item.session_id ?? t("accountPool.managerLogs.noSession")}
            </TableCell>
            <TableCell className="font-mono text-xs">{item.card_id}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function ManagerLogsPanel({ accessToken }: { accessToken: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<ManagerLogFilter>(EMPTY_FILTER);
  const [filterError, setFilterError] = useState(false);
  const [session, setSession] = useState("");
  const [card, setCard] = useState("");
  const [days, setDays] = useState("7");
  const daysToDelete = Number(days);
  const validDays = Number.isInteger(daysToDelete) && daysToDelete >= 1 && daysToDelete <= 365;
  const offset = filter.offset ?? 0;
  const logs = useQuery({
    queryKey: accountPoolQueryKeys.managerLogs(accessToken, filter),
    queryFn: ({ signal }) => getManagerLogs(accessToken, filter, signal),
    retry: false,
  });
  const purge = useMutation({
    mutationFn: (period: number) => deleteManagerLogs(accessToken, period),
    onSuccess: async () => {
      setFilter((current) => ({ ...current, offset: 0 }));
      await queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.managerLogsRoot(accessToken) });
    },
  });

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const sessionId = session.trim();
    const cardId = card.trim();
    const invalid = [sessionId, cardId].some((id) => id !== "" && !UUID.test(id));
    setFilterError(invalid);
    if (!invalid) setFilter({ ...EMPTY_FILTER, session_id: sessionId || undefined, card_id: cardId || undefined });
  };

  return (
    <section className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("accountPool.managerLogs.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.managerLogs.description")}</p>
      </header>
      <form
        className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-4"
        onSubmit={applyFilters}
      >
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="manager-log-session">{t("accountPool.managerLogs.sessionId")}</Label>
          <Input
            id="manager-log-session"
            value={session}
            onChange={(event) => setSession(event.target.value)}
            placeholder={t("accountPool.managerLogs.sessionIdPlaceholder")}
          />
        </div>
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="manager-log-card">{t("accountPool.managerLogs.cardId")}</Label>
          <Input
            id="manager-log-card"
            value={card}
            onChange={(event) => setCard(event.target.value)}
            placeholder={t("accountPool.managerLogs.cardIdPlaceholder")}
          />
        </div>
        <Button type="submit">{t("accountPool.managerLogs.filter")}</Button>
        <Button type="button" variant="outline" disabled={logs.isFetching} onClick={() => void logs.refetch()}>
          {t("accountPool.managerLogs.refresh")}
        </Button>
        {filterError ? (
          <p role="alert" className="w-full text-sm text-destructive">
            {t("accountPool.managerLogs.invalidUuid")}
          </p>
        ) : null}
      </form>
      <div className="overflow-hidden rounded-lg border bg-card">
        {logs.isError ? (
          <p role="alert" className="p-4 text-sm text-destructive">
            {t("accountPool.managerLogs.loadError")}
          </p>
        ) : null}
        {logs.isPending ? (
          <p role="status" className="p-4 text-sm text-muted-foreground">
            {t("accountPool.managerLogs.loading")}
          </p>
        ) : null}
        {!logs.isError && logs.data?.items.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">{t("accountPool.managerLogs.empty")}</p>
        ) : null}
        {!logs.isError && logs.data?.items.length ? <LogRows items={logs.data.items} /> : null}
        <footer className="flex items-center justify-between gap-2 border-t px-4 py-3">
          <span className="text-sm text-muted-foreground">
            {t("accountPool.managerLogs.pageOrder", { page: offset / PAGE_SIZE + 1 })}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={offset === 0 || logs.isFetching}
              onClick={() => setFilter({ ...filter, offset: offset - PAGE_SIZE })}
            >
              {t("accountPool.managerLogs.prevPage")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!logs.data?.has_more || logs.isFetching || offset >= 10_000}
              onClick={() => setFilter({ ...filter, offset: offset + PAGE_SIZE })}
            >
              {t("accountPool.managerLogs.nextPage")}
            </Button>
          </div>
        </footer>
      </div>
      <div className="space-y-3 rounded-lg border bg-card p-4">
        <h2 className="font-semibold">{t("accountPool.managerLogs.purgeTitle")}</h2>
        <p className="text-sm text-muted-foreground">{t("accountPool.managerLogs.purgeDescription")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor="manager-log-days">{t("accountPool.managerLogs.purgeDaysLabel")}</Label>
          <Input
            id="manager-log-days"
            type="number"
            min={1}
            max={365}
            step={1}
            className="w-24"
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
          <AlertDialog>
            <AlertDialogTrigger render={<Button variant="outline" />} disabled={!validDays || purge.isPending}>
              {t("accountPool.managerLogs.purgeButton")}
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {t("accountPool.managerLogs.purgeConfirmTitle", { days: daysToDelete })}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {t("accountPool.managerLogs.purgeConfirmDescription")}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t("accountPool.managerLogs.purgeCancel")}</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={() => purge.mutate(daysToDelete)}>
                  {t("accountPool.managerLogs.purgeConfirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
        {purge.isSuccess ? (
          <p role="status" className="text-sm">
            {t("accountPool.managerLogs.purgeSuccess", { count: purge.data.deleted })}
          </p>
        ) : null}
        {purge.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {t("accountPool.managerLogs.purgeError")}
          </p>
        ) : null}
      </div>
    </section>
  );
}
