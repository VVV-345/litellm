/** 本文件提供号池插件管理和商城入口，只允许安装声明为 sidecar 的插件。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/lib/toast";

import {
  installAccountPoolPlugin,
  listAccountPoolPluginStore,
  listAccountPoolPlugins,
  setAccountPoolPluginEnabled,
  uninstallAccountPoolPlugin,
  type AccountPoolPluginManifest,
} from "./AccountPoolManagementApi";

export function AccountPoolPluginsPanel({ accessToken }: { accessToken: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const installed = useQuery({
    queryKey: ["account-pool", "plugins", accessToken],
    queryFn: () => listAccountPoolPlugins(accessToken),
    retry: false,
  });
  const store = useQuery({
    queryKey: ["account-pool", "plugin-store", accessToken],
    queryFn: () => listAccountPoolPluginStore(accessToken),
    retry: false,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["account-pool", "plugins", accessToken] });
  };
  const install = useMutation({
    mutationFn: (manifest: AccountPoolPluginManifest) => installAccountPoolPlugin(accessToken, manifest),
    onSuccess: () => {
      toast.success(t("accountPool.plugins.installed"));
      invalidate();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const toggle = useMutation({
    mutationFn: ({ pluginId, enabled }: { pluginId: string; enabled: boolean }) =>
      setAccountPoolPluginEnabled(accessToken, pluginId, enabled),
    onSuccess: invalidate,
    onError: (error: Error) => toast.fromError(error),
  });
  const uninstall = useMutation({
    mutationFn: (pluginId: string) => uninstallAccountPoolPlugin(accessToken, pluginId),
    onSuccess: () => {
      toast.success(t("accountPool.plugins.uninstalled"));
      invalidate();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const installedById = new Map((installed.data ?? []).map((item) => [item.manifest.plugin_id, item]));
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.plugins.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.plugins.description")}</p>
      </div>
      {installed.isError && <p role="alert">{t("accountPool.plugins.loadFailed")}</p>}
      <div className="grid gap-3">
        {(store.data ?? []).map((manifest) => {
          const record = installedById.get(manifest.plugin_id);
          return (
            <Card key={manifest.plugin_id}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <CardTitle className="text-base">{manifest.display_name}</CardTitle>
                  <Badge variant={record?.state === "enabled" ? "secondary" : "outline"}>
                    {record?.state ?? t("accountPool.plugins.available")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center justify-between gap-3 text-sm">
                <span className="text-muted-foreground">v{manifest.version} · {manifest.runtime}</span>
                <div className="flex gap-2">
                  {!record && <Button size="sm" onClick={() => install.mutate(manifest)}>{t("accountPool.plugins.install")}</Button>}
                  {record && <Button size="sm" variant="outline" onClick={() => toggle.mutate({ pluginId: manifest.plugin_id, enabled: record.state !== "enabled" })}>{record.state === "enabled" ? t("accountPool.plugins.disable") : t("accountPool.plugins.enable")}</Button>}
                  {record && <Button size="sm" variant="destructive" onClick={() => uninstall.mutate(manifest.plugin_id)}>{t("accountPool.plugins.uninstall")}</Button>}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      {(store.data ?? []).length === 0 && <p className="text-sm text-muted-foreground">{t("accountPool.plugins.storeEmpty")}</p>}
    </div>
  );
}
