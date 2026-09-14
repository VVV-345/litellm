"use client";

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/lib/toast";

import { createDesktopTicket, getDesktopTicket, type DesktopAction, type PolicyView } from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import {
  buildDesktopCompanionUrl,
  desktopInstanceSchema,
  desktopStatusSchema,
  desktopWslResultSchema,
  openDesktopCompanion,
  type DesktopInstance,
  type DesktopStatus,
} from "./accountPoolDesktopCompanion";

const wait = (milliseconds: number): Promise<void> =>
  new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));

const instanceLabel = (instance: DesktopInstance, fallback: string): string =>
  instance.name.trim() || (instance.isDefault ? fallback : instance.id);

export function AccountPoolDesktopPanel({
  accessToken,
  environments,
  policies,
}: {
  accessToken: string;
  environments: readonly AccountPoolEnvironment[];
  policies: readonly PolicyView[];
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selectedInstanceId, setSelectedInstanceId] = useState("");
  const [selectedCardId, setSelectedCardId] = useState("");
  const [wslEnabled, setWslEnabled] = useState(false);
  const [wslConfigDir, setWslConfigDir] = useState("");
  const codexCards = useMemo(
    () => environments.filter((environment) => environment.supplier === "openai_codex"),
    [environments],
  );
  const effectiveCardId = codexCards.some((environment) => environment.id === selectedCardId)
    ? selectedCardId
    : codexCards[0]?.id || "";
  const selectedPolicy = policies.find((policy) => policy.card_id === effectiveCardId)?.policy?.codex ?? null;
  const compactActionDisabled = [
    busy !== null,
    status === null,
    selectedInstanceId.length === 0,
    selectedPolicy === null,
  ].some(Boolean);

  const runAction = async (action: DesktopAction): Promise<Record<string, unknown>> => {
    const ticket = await createDesktopTicket(accessToken, action);
    openDesktopCompanion(buildDesktopCompanionUrl(globalThis.location.origin, ticket));
    while (true) {
      await wait(750);
      const current = await getDesktopTicket(accessToken, ticket.ticket_id);
      if (current.status === "succeeded") return current.result ?? {};
      if (current.status === "failed") throw new Error(current.error || t("accountPool.desktop.operationFailed"));
      if (current.status === "cancelled") throw new Error(t("accountPool.desktop.operationCancelled"));
      if (current.status === "expired") throw new Error(t("accountPool.desktop.ticketExpired"));
    }
    throw new Error(t("accountPool.desktop.cockpitNotDetected"));
  };

  const perform = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    try {
      await action();
    } catch (error) {
      toast.fromError(error);
    } finally {
      setBusy(null);
    }
  };

  const refresh = () =>
    perform("status", async () => {
      const next = desktopStatusSchema.parse(await runAction({ kind: "status" }));
      setStatus(next);
      setSelectedInstanceId((current) =>
        next.codex_instances.some((instance) => instance.id === current) ? current : next.codex_instances[0]?.id || "",
      );
      setWslEnabled(next.codex_wsl.enabled);
      setWslConfigDir(next.codex_wsl.config_dir);
      toast.success(t("accountPool.desktop.statusLoaded"));
    });

  const updateInstance = (application: "codex" | "cursor", updated: DesktopInstance) => {
    setStatus((current) => {
      if (current === null) return current;
      const key = application === "codex" ? "codex_instances" : "cursor_instances";
      return { ...current, [key]: current[key].map((instance) => (instance.id === updated.id ? updated : instance)) };
    });
  };

  const toggleInstance = (application: "codex" | "cursor", instance: DesktopInstance) =>
    perform(`${application}:${instance.id}`, async () => {
      const action: DesktopAction = {
        kind: instance.running ? "stop_instance" : "start_instance",
        application,
        instance_id: instance.id,
      };
      const updated = desktopInstanceSchema.parse(await runAction(action));
      updateInstance(application, updated);
      toast.success(
        t(instance.running ? "accountPool.desktop.instanceStopped" : "accountPool.desktop.instanceStarted", {
          name: instanceLabel(instance, t("accountPool.desktop.defaultInstance")),
        }),
      );
    });

  const applyCompact = () =>
    perform("compact", async () => {
      if (!selectedInstanceId || selectedPolicy === null) {
        throw new Error(t("accountPool.desktop.selectCompactTargets"));
      }
      const action: DesktopAction = {
        kind: "apply_codex_compact",
        instance_id: selectedInstanceId,
        enabled: selectedPolicy.compact_ui,
        model_context_window: selectedPolicy.model_context_window,
        auto_compact_token_limit: selectedPolicy.model_auto_compact_token_limit,
        experimental_context_management: selectedPolicy.experimental_context_management,
      };
      await runAction(action);
      toast.success(t("accountPool.desktop.compactApplied"));
    });

  const saveWsl = () =>
    perform("wsl", async () => {
      const result = await runAction({
        kind: "configure_codex_wsl",
        enabled: wslEnabled,
        config_dir: wslEnabled ? wslConfigDir.trim() : "",
      });
      const saved = desktopWslResultSchema.parse(result);
      setWslEnabled(saved.enabled);
      setWslConfigDir(saved.config_dir);
      toast.success(t("accountPool.desktop.wslSaved"));
    });

  const renderInstances = (application: "codex" | "cursor", instances: DesktopInstance[]) => (
    <div className="grid gap-3 md:grid-cols-2">
      {instances.map((instance) => (
        <div key={instance.id} className="rounded-lg border bg-background p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-medium">
                {instanceLabel(instance, t("accountPool.desktop.defaultInstance"))}
              </p>
              <p className="mt-1 truncate text-xs text-muted-foreground">{instance.userDataDir}</p>
            </div>
            <Badge variant={instance.running ? "default" : "outline"}>
              {t(instance.running ? "accountPool.desktop.running" : "accountPool.desktop.stopped")}
            </Badge>
          </div>
          <Button
            type="button"
            size="sm"
            variant={instance.running ? "outline" : "default"}
            className="mt-4"
            disabled={busy !== null}
            onClick={() => void toggleInstance(application, instance)}
          >
            {t(instance.running ? "accountPool.desktop.stop" : "accountPool.desktop.start")}
          </Button>
        </div>
      ))}
      {instances.length === 0 && (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          {t("accountPool.desktop.noInstances")}
        </p>
      )}
    </div>
  );

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold">{t("accountPool.desktop.title")}</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
              {t("accountPool.desktop.description")}
            </p>
          </div>
          <Button type="button" onClick={() => void refresh()} disabled={busy !== null}>
            {t("accountPool.desktop.refresh")}
          </Button>
        </div>
      </section>

      {status !== null && (
        <>
          <section className="space-y-3 rounded-lg border bg-card p-5">
            <h3 className="font-semibold">Codex</h3>
            {renderInstances("codex", status.codex_instances)}
          </section>
          <section className="space-y-3 rounded-lg border bg-card p-5">
            <h3 className="font-semibold">Cursor</h3>
            {renderInstances("cursor", status.cursor_instances)}
          </section>
        </>
      )}

      <section className="space-y-4 rounded-lg border bg-card p-5">
        <div>
          <h3 className="font-semibold">{t("accountPool.desktop.compactTitle")}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("accountPool.desktop.compactDescription")}</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="account-pool-desktop-instance">{t("accountPool.desktop.localCodexInstance")}</Label>
            <Select
              value={selectedInstanceId}
              onValueChange={(value) => value !== null && setSelectedInstanceId(value)}
            >
              <SelectTrigger id="account-pool-desktop-instance">
                <SelectValue placeholder={t("accountPool.desktop.selectInstance")} />
              </SelectTrigger>
              <SelectContent>
                {(status?.codex_instances ?? []).map((instance) => (
                  <SelectItem key={instance.id} value={instance.id}>
                    {instanceLabel(instance, t("accountPool.desktop.defaultInstance"))}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-desktop-card">{t("accountPool.desktop.sourceCard")}</Label>
            <Select value={effectiveCardId} onValueChange={(value) => value !== null && setSelectedCardId(value)}>
              <SelectTrigger id="account-pool-desktop-card">
                <SelectValue placeholder={t("accountPool.desktop.selectCard")} />
              </SelectTrigger>
              <SelectContent>
                {codexCards.map((environment) => (
                  <SelectItem key={environment.id} value={environment.id}>
                    {environment.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {selectedPolicy !== null && (
          <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">
            {t("accountPool.desktop.compactSummary", {
              enabled: selectedPolicy.compact_ui ? t("accountPool.desktop.enabled") : t("accountPool.desktop.disabled"),
              window: selectedPolicy.model_context_window ?? 1_000_000,
              limit: selectedPolicy.model_auto_compact_token_limit ?? 900_000,
            })}
          </p>
        )}
        <Button type="button" disabled={compactActionDisabled} onClick={() => void applyCompact()}>
          {t("accountPool.desktop.applyCompact")}
        </Button>
      </section>

      <section className="space-y-4 rounded-lg border bg-card p-5">
        <div>
          <h3 className="font-semibold">{t("accountPool.desktop.wslTitle")}</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("accountPool.desktop.wslDescription")}</p>
        </div>
        <div className="flex items-center justify-between gap-4 rounded-md border p-3">
          <Label htmlFor="account-pool-wsl-enabled">{t("accountPool.desktop.wslEnabled")}</Label>
          <Switch
            id="account-pool-wsl-enabled"
            checked={wslEnabled}
            disabled={status === null || busy !== null}
            onCheckedChange={(value) => setWslEnabled(value === true)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="account-pool-wsl-dir">{t("accountPool.desktop.wslDirectory")}</Label>
          <Input
            id="account-pool-wsl-dir"
            value={wslConfigDir}
            disabled={status === null || busy !== null || !wslEnabled}
            placeholder={String.raw`\\wsl.localhost\Ubuntu\home\user\.codex`}
            onChange={(event) => setWslConfigDir(event.target.value)}
          />
        </div>
        <Button type="button" disabled={status === null || busy !== null} onClick={() => void saveWsl()}>
          {t("accountPool.desktop.saveWsl")}
        </Button>
      </section>
    </div>
  );
}
