/** 本文件提供号池插件管理和商城入口，只允许安装声明为 sidecar 的插件。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";

import {
  installCardAccountPoolPlugin,
  listCardAccountPoolPluginStore,
  listCardAccountPoolPlugins,
  getCardAccountPoolPluginConfig,
  putCardAccountPoolPluginConfig,
  setCardAccountPoolPluginEnabled,
  uninstallCardAccountPoolPlugin,
} from "./AccountPoolManagementApi";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

type RuntimePlugin = {
  id: string;
  name: string;
  description: string;
  version: string;
  installed: boolean;
  enabled: boolean;
  effectiveEnabled: boolean;
  updateAvailable: boolean;
  configured: boolean;
  registered: boolean;
  installType: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const readString = (value: unknown, fallback = "") => (typeof value === "string" ? value : fallback);

const readBoolean = (value: unknown, fallback = false) => (typeof value === "boolean" ? value : fallback);

const runtimePlugins = (payload: Record<string, unknown> | undefined): RuntimePlugin[] => {
  const values: unknown = payload?.plugins;
  if (!Array.isArray(values)) return [];
  return values.flatMap((value) => {
    if (!isRecord(value) || typeof value.id !== "string") return [];
    return [
      {
        id: value.id,
        name: readString(value.name, value.id),
        description: readString(value.description),
        version: readString(value.version, "-"),
        installed: readBoolean(value.installed),
        enabled: readBoolean(value.enabled),
        effectiveEnabled: readBoolean(value.effective_enabled),
        updateAvailable: readBoolean(value.update_available),
        configured: readBoolean(value.configured),
        registered: readBoolean(value.registered),
        installType: readString(value.install_type, "sidecar"),
      },
    ];
  });
};

export function AccountPoolPluginsPanel({
  accessToken,
  environments,
}: {
  accessToken: string;
  environments: AccountPoolEnvironment[];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const cards = useMemo(
    () => environments.filter((environment) => environment.channel === "cliproxyapi"),
    [environments],
  );
  const [selectedCardId, setSelectedCardId] = useState<string | null>(cards[0]?.id ?? null);
  const [configPluginId, setConfigPluginId] = useState<string | null>(null);
  const [configText, setConfigText] = useState("{}");
  const [configDirty, setConfigDirty] = useState(false);
  const activeCardId = cards.some((card) => card.id === selectedCardId) ? selectedCardId : cards[0]?.id ?? null;
  const selectedCard = cards.find((card) => card.id === activeCardId) ?? null;
  const configQuery = useQuery({
    queryKey: ["account-pool", "card-plugin-config", accessToken, activeCardId, configPluginId],
    queryFn: () => getCardAccountPoolPluginConfig(accessToken, activeCardId!, configPluginId!),
    enabled: activeCardId !== null && configPluginId !== null,
    retry: false,
  });
  const installed = useQuery({
    queryKey: ["account-pool", "card-plugins", accessToken, activeCardId],
    queryFn: () => listCardAccountPoolPlugins(accessToken, activeCardId!),
    enabled: activeCardId !== null,
    retry: false,
  });
  const store = useQuery({
    queryKey: ["account-pool", "card-plugin-store", accessToken, activeCardId],
    queryFn: () => listCardAccountPoolPluginStore(accessToken, activeCardId!),
    enabled: activeCardId !== null,
    retry: false,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "card-plugins", accessToken, activeCardId] });
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "card-plugin-store", accessToken, activeCardId] });
  };
  const install = useMutation({
    mutationFn: (plugin: RuntimePlugin) =>
      installCardAccountPoolPlugin(accessToken, activeCardId!, plugin.id, plugin.version),
    onSuccess: () => {
      toast.success(t("accountPool.plugins.installed"));
      invalidate();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const toggle = useMutation({
    mutationFn: ({ pluginId, enabled }: { pluginId: string; enabled: boolean }) =>
      setCardAccountPoolPluginEnabled(accessToken, activeCardId!, pluginId, enabled),
    onSuccess: invalidate,
    onError: (error: Error) => toast.fromError(error),
  });
  const uninstall = useMutation({
    mutationFn: (pluginId: string) => uninstallCardAccountPoolPlugin(accessToken, activeCardId!, pluginId),
    onSuccess: () => {
      toast.success(t("accountPool.plugins.uninstalled"));
      invalidate();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const configMutation = useMutation({
    mutationFn: () => {
      if (activeCardId === null || configPluginId === null) throw new Error("plugin card is not selected");
      const parsed: unknown = JSON.parse(configText);
      if (!isRecord(parsed)) throw new Error("plugin configuration must be a JSON object");
      return putCardAccountPoolPluginConfig(accessToken, activeCardId, configPluginId, parsed);
    },
    onSuccess: () => {
      toast.success(t("accountPool.plugins.configSaved"));
      setConfigDirty(false);
      void configQuery.refetch();
      invalidate();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const availablePlugins = useMemo(() => runtimePlugins(store.data), [store.data]);
  const installedPlugins = useMemo(() => runtimePlugins(installed.data), [installed.data]);
  const installedById = useMemo(
    () => new Map(installedPlugins.map((plugin) => [plugin.id, plugin])),
    [installedPlugins],
  );
  const plugins = useMemo(() => {
    const merged = new Map(availablePlugins.map((plugin) => [plugin.id, plugin]));
    installedPlugins.forEach((plugin) => merged.set(plugin.id, { ...merged.get(plugin.id), ...plugin }));
    return [...merged.values()];
  }, [availablePlugins, installedPlugins]);
  const loadedConfigText = configQuery.data ? JSON.stringify(configQuery.data, null, 2) : "{}";
  const editorText = configDirty ? configText : loadedConfigText;
  const openConfig = (pluginId: string) => {
    setConfigPluginId(pluginId);
    setConfigText("{}");
    setConfigDirty(false);
  };

  if (cards.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">{t("accountPool.plugins.noCards")}</CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.plugins.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.plugins.description")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-3 rounded-md border p-4">
        <span className="text-sm font-medium">{t("accountPool.plugins.card")}</span>
        <Select value={selectedCardId ?? undefined} onValueChange={setSelectedCardId}>
          <SelectTrigger className="w-full sm:w-80" aria-label={t("accountPool.plugins.card")}>
            <SelectValue placeholder={t("accountPool.plugins.selectCard")} />
          </SelectTrigger>
          <SelectContent>
            {cards.map((card) => (
              <SelectItem key={card.id} value={card.id}>
                {card.name} · {card.supplier}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedCard && <Badge variant="outline">{selectedCard.status}</Badge>}
      </div>
      {installed.isError && <p role="alert">{t("accountPool.plugins.loadFailed")}</p>}
      <div className="grid gap-3">
        {plugins.map((plugin) => {
          const record = installedById.get(plugin.id);
          const isInstalled = record !== undefined || plugin.installed;
          const isEnabled = record?.effectiveEnabled ?? plugin.effectiveEnabled;
          const stateLabel = (() => {
            if (isEnabled) return t("accountPool.plugins.enable");
            if (isInstalled) return t("accountPool.plugins.disable");
            return t("accountPool.plugins.available");
          })();
          return (
            <Card key={plugin.id}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <CardTitle className="text-base">{plugin.name}</CardTitle>
                  <Badge variant={isEnabled ? "secondary" : "outline"}>
                    {stateLabel}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center justify-between gap-3 text-sm">
                <span className="text-muted-foreground">v{plugin.version} · {plugin.installType}</span>
                <div className="flex gap-2">
                  {!isInstalled && <Button size="sm" onClick={() => install.mutate(plugin)}>{t("accountPool.plugins.install")}</Button>}
                  {isInstalled && <Button size="sm" variant="outline" onClick={() => toggle.mutate({ pluginId: plugin.id, enabled: !isEnabled })}>{isEnabled ? t("accountPool.plugins.disable") : t("accountPool.plugins.enable")}</Button>}
                  {isInstalled && <Button size="sm" variant="outline" onClick={() => openConfig(plugin.id)}>{t("accountPool.plugins.configure")}</Button>}
                  {isInstalled && <Button size="sm" variant="destructive" onClick={() => uninstall.mutate(plugin.id)}>{t("accountPool.plugins.uninstall")}</Button>}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      {plugins.length === 0 && <p className="text-sm text-muted-foreground">{t("accountPool.plugins.storeEmpty")}</p>}
      <Dialog
        open={configPluginId !== null}
        onOpenChange={(open) => {
          if (!open && !configMutation.isPending) setConfigPluginId(null);
        }}
      >
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("accountPool.plugins.configTitle")}</DialogTitle>
            <DialogDescription>
              {t("accountPool.plugins.configDescription", {
                name: plugins.find((plugin) => plugin.id === configPluginId)?.name ?? configPluginId,
              })}
            </DialogDescription>
          </DialogHeader>
          {configQuery.isError && <p role="alert">{t("accountPool.plugins.configLoadFailed")}</p>}
          <Textarea
            value={editorText}
            onChange={(event) => {
              setConfigText(event.target.value);
              setConfigDirty(true);
            }}
            disabled={configQuery.isPending || configMutation.isPending}
            className="min-h-64 font-mono text-xs"
            aria-label={t("accountPool.plugins.configTitle")}
            spellCheck={false}
          />
          <DialogFooter>
            <Button
              onClick={() => configMutation.mutate()}
              disabled={configQuery.isPending || configMutation.isPending || !configDirty}
            >
              {configMutation.isPending ? t("accountPool.plugins.configSaving") : t("accountPool.plugins.configSave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
