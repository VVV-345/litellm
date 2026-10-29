/** 提供 AdsPower 环境选择与手动 ISP 导入，应用出口继续使用现有端口配置。 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { extractProxyErrorMessage } from "@/lib/http/client";
import { listAdsPowerProfiles, syncAdsPowerProxies, type AdsPowerProfiles } from "../../api/AdsPowerApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

export function AdsPowerSyncPanel({ accessToken }: { accessToken: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [page, setPage] = useState<AdsPowerProfiles | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const load = useMutation({
    mutationFn: (number: number) => listAdsPowerProfiles(accessToken, number),
    onSuccess: (result) => {
      setPage(result);
      setSelected([]);
    },
  });
  const sync = useMutation({
    mutationFn: () => syncAdsPowerProxies(accessToken, selected),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.proxyIsps(accessToken) });
      setSelected([]);
    },
  });
  const busy = load.isPending || sync.isPending;
  const error = load.error ?? sync.error;
  const readPage = (number: number) => {
    sync.reset();
    load.mutate(number);
  };
  return (
    <section className="mb-4 grid gap-3 rounded-md border p-4" aria-label="AdsPower">
      <h3 className="text-sm font-medium">AdsPower</h3>
      <p className="text-xs text-muted-foreground">{t("accountPool.adspower.help")}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => readPage(1)}>
          {t("accountPool.adspower.load")}
        </Button>
        <Button
          size="sm"
          disabled={busy || selected.length === 0 || selected.length > 20}
          onClick={() => sync.mutate()}
        >
          {t("accountPool.adspower.sync")}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {extractProxyErrorMessage(error)}
        </p>
      )}
      {page && (
        <>
          <div className="grid max-h-64 gap-2 overflow-auto">
            {page.profiles.map((profile) => (
              <label key={profile.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={selected.includes(profile.id)}
                  disabled={busy || !profile.syncable}
                  onChange={(event) => {
                    sync.reset();
                    setSelected(
                      event.target.checked ? [...selected, profile.id] : selected.filter((id) => id !== profile.id),
                    );
                  }}
                />
                <span>
                  {profile.name} ({profile.id}) —{" "}
                  {profile.syncable
                    ? `${profile.protocol}://${profile.host}:${profile.port}`
                    : t("accountPool.adspower.unsupported")}
                </span>
              </label>
            ))}
            {page.profiles.length === 0 && <p className="text-sm">{t("accountPool.adspower.empty")}</p>}
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" disabled={busy || page.page <= 1} onClick={() => readPage(page.page - 1)}>
              {t("accountPool.adspower.previous")}
            </Button>
            <span className="self-center text-xs">{page.page}</span>
            <Button size="sm" variant="ghost" disabled={busy || !page.has_more} onClick={() => readPage(page.page + 1)}>
              {t("accountPool.adspower.next")}
            </Button>
          </div>
        </>
      )}
      {sync.data && (
        <div role="status" className="text-sm">
          <p>{t("accountPool.adspower.synced", { count: sync.data.synced.length })}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.adspower.applyHelp")}</p>
        </div>
      )}
    </section>
  );
}
