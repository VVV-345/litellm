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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import {
  accountPoolPluginSourceErrors,
  accountPoolRuntimePlugins,
  type AccountPoolRuntimePlugin,
} from "./AccountPoolPluginRuntime";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

const pluginStateTranslationKey = (plugin: AccountPoolRuntimePlugin) => {
  if (plugin.effectiveEnabled) return "accountPool.plugins.running";
  if (plugin.installed) return "accountPool.plugins.installedState";
  return "accountPool.plugins.available";
};

const resolveActiveCardId = (cards: AccountPoolEnvironment[], selectedCardId: string | null) =>
  cards.find((card) => card.id === selectedCardId)?.id ?? cards[0]?.id ?? null;

const runtimePluginsEnabled = (
  store: Record<string, unknown> | undefined,
  installed: Record<string, unknown> | undefined,
) => store?.plugins_enabled === true || installed?.plugins_enabled === true;

const formatPluginConfig = (config: Record<string, unknown> | undefined) =>
  config ? JSON.stringify(config, null, 2) : "{}";

const pluginDisplayName = (plugins: AccountPoolRuntimePlugin[], pluginId: string | null) =>
  plugins.find((plugin) => plugin.id === pluginId)?.name ?? pluginId ?? "";

type PluginCardProps = {
  plugin: AccountPoolRuntimePlugin;
  onInstall: (version: string) => void;
  onSelectVersion: () => void;
  onToggle: () => void;
  onConfigure: () => void;
  onUninstall: () => void;
};

function AccountPoolPluginCard({
  plugin,
  onInstall,
  onSelectVersion,
  onToggle,
  onConfigure,
  onUninstall,
}: PluginCardProps) {
  const { t } = useTranslation();
  const stateLabel = t(pluginStateTranslationKey(plugin));
  const details = [
    [t("accountPool.plugins.storeVersion"), plugin.storeVersion || "-"],
    [t("accountPool.plugins.installedVersion"), plugin.installedVersion || "-"],
    [t("accountPool.plugins.source"), plugin.sourceName || plugin.sourceId || "-"],
    [t("accountPool.plugins.installType"), plugin.installType],
    [t("accountPool.plugins.configured"), plugin.configured],
    [t("accountPool.plugins.registered"), plugin.registered],
    [t("accountPool.plugins.enabled"), plugin.enabled],
    [t("accountPool.plugins.effectiveEnabled"), plugin.effectiveEnabled],
  ] as const;
  const manageable = plugin.installed || plugin.configured;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">{plugin.name}</CardTitle>
          <Badge variant={plugin.effectiveEnabled ? "secondary" : "outline"}>{stateLabel}</Badge>
        </div>
        {plugin.description && <p className="text-sm text-muted-foreground">{plugin.description}</p>}
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <div className="grid overflow-hidden rounded-md border sm:grid-cols-2 lg:grid-cols-4">
          {details.map(([label, value]) => (
            <div
              key={String(label)}
              className="grid gap-1 border-b p-3 last:border-b-0 sm:border-r lg:[&:nth-child(4n)]:border-r-0"
            >
              <span className="text-xs text-muted-foreground">{String(label)}</span>
              <span className="font-medium">
                {typeof value === "boolean"
                  ? t(value ? "accountPool.plugins.yes" : "accountPool.plugins.no")
                  : String(value)}
              </span>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {!plugin.installed && plugin.storeVersion && (
            <Button size="sm" onClick={() => onInstall(plugin.storeVersion)}>
              {t("accountPool.plugins.install")}
            </Button>
          )}
          {plugin.updateAvailable && plugin.storeVersion && (
            <Button size="sm" onClick={() => onInstall(plugin.storeVersion)}>
              {t("accountPool.plugins.update")}
            </Button>
          )}
          {plugin.installed && (
            <Button size="sm" variant="outline" onClick={onSelectVersion}>
              {t("accountPool.plugins.versionAction")}
            </Button>
          )}
          {manageable && (
            <Button size="sm" variant="outline" onClick={onToggle}>
              {plugin.enabled ? t("accountPool.plugins.disable") : t("accountPool.plugins.enable")}
            </Button>
          )}
          {manageable && (
            <Button size="sm" variant="outline" onClick={onConfigure}>
              {t("accountPool.plugins.configure")}
            </Button>
          )}
          {manageable && (
            <Button size="sm" variant="destructive" onClick={onUninstall}>
              {t("accountPool.plugins.uninstall")}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

type PluginVersionDialogProps = {
  plugin: AccountPoolRuntimePlugin | null;
  version: string;
  pending: boolean;
  onVersionChange: (version: string) => void;
  onInstall: () => void;
  onClose: () => void;
};

function PluginVersionDialog({
  plugin,
  version,
  pending,
  onVersionChange,
  onInstall,
  onClose,
}: PluginVersionDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={plugin !== null} onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("accountPool.plugins.versionTitle")}</DialogTitle>
          <DialogDescription>
            {t("accountPool.plugins.versionDescription", { name: plugin?.name ?? "" })}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <Label htmlFor="account-pool-plugin-version">{t("accountPool.plugins.version")}</Label>
          <Input
            id="account-pool-plugin-version"
            value={version}
            onChange={(event) => onVersionChange(event.target.value)}
            placeholder={plugin?.storeVersion}
            disabled={pending}
          />
        </div>
        <DialogFooter>
          <Button onClick={onInstall} disabled={pending || !version.trim()}>
            {pending ? t("accountPool.plugins.installing") : t("accountPool.plugins.installVersion")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type PluginConfigDialogProps = {
  pluginName: string;
  open: boolean;
  text: string;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  failed: boolean;
  onTextChange: (text: string) => void;
  onSave: () => void;
  onClose: () => void;
};

function PluginConfigDialog({
  pluginName,
  open,
  text,
  loading,
  saving,
  dirty,
  failed,
  onTextChange,
  onSave,
  onClose,
}: PluginConfigDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && !saving && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("accountPool.plugins.configTitle")}</DialogTitle>
          <DialogDescription>{t("accountPool.plugins.configDescription", { name: pluginName })}</DialogDescription>
        </DialogHeader>
        {failed && <p role="alert">{t("accountPool.plugins.configLoadFailed")}</p>}
        <Textarea
          value={text}
          onChange={(event) => onTextChange(event.target.value)}
          disabled={loading || saving}
          className="min-h-64 font-mono text-xs"
          aria-label={t("accountPool.plugins.configTitle")}
          spellCheck={false}
        />
        <DialogFooter>
          <Button onClick={onSave} disabled={loading || saving || !dirty}>
            {saving ? t("accountPool.plugins.configSaving") : t("accountPool.plugins.configSave")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

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
  const [versionPlugin, setVersionPlugin] = useState<AccountPoolRuntimePlugin | null>(null);
  const [versionText, setVersionText] = useState("");
  const activeCardId = resolveActiveCardId(cards, selectedCardId);
  const selectedCard = cards.find((card) => card.id === activeCardId) ?? null;
  const configQueryOptions = {
    queryKey: ["account-pool", "card-plugin-config", accessToken, activeCardId, configPluginId],
    queryFn: () => getCardAccountPoolPluginConfig(accessToken, activeCardId!, configPluginId!),
    enabled: activeCardId !== null && configPluginId !== null,
    retry: false,
  } as const;
  const installedQueryOptions = {
    queryKey: ["account-pool", "card-plugins", accessToken, activeCardId],
    queryFn: () => listCardAccountPoolPlugins(accessToken, activeCardId!),
    enabled: activeCardId !== null,
    retry: false,
  } as const;
  const storeQueryOptions = {
    queryKey: ["account-pool", "card-plugin-store", accessToken, activeCardId],
    queryFn: () => listCardAccountPoolPluginStore(accessToken, activeCardId!),
    enabled: activeCardId !== null,
    retry: false,
  } as const;
  const configQuery = useQuery(configQueryOptions);
  const installed = useQuery(installedQueryOptions);
  const store = useQuery(storeQueryOptions);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "card-plugins", accessToken, activeCardId] });
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "card-plugin-store", accessToken, activeCardId] });
  };
  const install = useMutation({
    mutationFn: ({ plugin, version }: { plugin: AccountPoolRuntimePlugin; version: string }) =>
      installCardAccountPoolPlugin(accessToken, activeCardId!, plugin.id, {
        version,
        ...(plugin.sourceId ? { source: plugin.sourceId } : {}),
      }),
    onSuccess: () => {
      toast.success(t("accountPool.plugins.installed"));
      setVersionPlugin(null);
      setVersionText("");
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
  const plugins = useMemo(() => accountPoolRuntimePlugins(store.data, installed.data), [store.data, installed.data]);
  const sourceErrors = useMemo(() => accountPoolPluginSourceErrors(store.data), [store.data]);
  const pluginsEnabled = runtimePluginsEnabled(store.data, installed.data);
  const loadedConfigText = formatPluginConfig(configQuery.data);
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
        <Select value={activeCardId ?? undefined} onValueChange={setSelectedCardId}>
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
        <Badge variant={pluginsEnabled ? "secondary" : "outline"}>
          {pluginsEnabled ? t("accountPool.plugins.globalEnabled") : t("accountPool.plugins.globalDisabled")}
        </Badge>
      </div>
      {installed.isError && <p role="alert">{t("accountPool.plugins.loadFailed")}</p>}
      {sourceErrors.map((error) => (
        <p key={`${error.sourceId}:${error.message}`} role="alert" className="text-sm text-destructive">
          {error.sourceName || error.sourceId}: {error.message}
        </p>
      ))}
      <div className="grid gap-3">
        {plugins.map((plugin) => (
          <AccountPoolPluginCard
            key={plugin.key}
            plugin={plugin}
            onInstall={(version) => install.mutate({ plugin, version })}
            onSelectVersion={() => {
              setVersionPlugin(plugin);
              setVersionText(plugin.installedVersion);
            }}
            onToggle={() => toggle.mutate({ pluginId: plugin.id, enabled: !plugin.enabled })}
            onConfigure={() => openConfig(plugin.id)}
            onUninstall={() => uninstall.mutate(plugin.id)}
          />
        ))}
      </div>
      {plugins.length === 0 && <p className="text-sm text-muted-foreground">{t("accountPool.plugins.storeEmpty")}</p>}
      <PluginVersionDialog
        plugin={versionPlugin}
        version={versionText}
        pending={install.isPending}
        onVersionChange={setVersionText}
        onInstall={() => {
          if (versionPlugin) install.mutate({ plugin: versionPlugin, version: versionText.trim() });
        }}
        onClose={() => setVersionPlugin(null)}
      />
      <PluginConfigDialog
        pluginName={pluginDisplayName(plugins, configPluginId)}
        open={configPluginId !== null}
        text={editorText}
        loading={configQuery.isPending}
        saving={configMutation.isPending}
        dirty={configDirty}
        failed={configQuery.isError}
        onTextChange={(text) => {
          setConfigText(text);
          setConfigDirty(true);
        }}
        onSave={() => configMutation.mutate()}
        onClose={() => setConfigPluginId(null)}
      />
    </div>
  );
}
