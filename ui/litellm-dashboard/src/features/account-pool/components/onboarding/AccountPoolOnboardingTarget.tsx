/** 配置 OAuth 可用数量目标并展示真实缺口，不把待授权计为成功。 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import { listOnboardingTargets, saveOnboardingTarget, type OnboardingTarget } from "../../api/AccountPoolOnboardingApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

export function AccountPoolOnboardingTarget({
  accessToken,
  supplier,
}: {
  accessToken: string;
  supplier: OnboardingTarget["supplier"];
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [draft, setDraft] = useState<OnboardingTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const queryKey = accountPoolQueryKeys.onboardingTargets(accessToken);
  const query = useQuery({ queryKey, queryFn: () => listOnboardingTargets(accessToken), refetchInterval: 5000 });
  const saved = query.data?.find((target) => target.supplier === supplier);
  const value = draft?.supplier === supplier ? draft : saved ?? { supplier, count: 0, enabled: false, model: "" };
  const save = async () => {
    setBusy(true);
    try {
      const body = { supplier, count: value.count, enabled: value.enabled, model: value.model };
      await saveOnboardingTarget(accessToken, body);
      await client.invalidateQueries({ queryKey });
      setDraft(null);
      toast.success(t("accountPool.onboarding.target.saveSuccess"));
    } catch {
      toast.error(t("accountPool.onboarding.target.saveError"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("accountPool.onboarding.target.title")}</CardTitle>
        <CardDescription>{t("accountPool.onboarding.target.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isError && (
          <p role="alert" className="text-destructive">
            {t("accountPool.onboarding.target.loadError")}
          </p>
        )}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            [t("accountPool.onboarding.target.metricReady"), saved?.ready ?? 0],
            [t("accountPool.onboarding.target.metricInProgress"), saved?.in_progress ?? 0],
            [t("accountPool.onboarding.target.metricStandby"), saved?.standby ?? 0],
            [t("accountPool.onboarding.target.metricShortage"), saved?.shortage ?? 0],
          ].map(([label, count]) => (
            <div key={label} className="rounded-xl border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-2 text-2xl font-semibold tabular-nums">{count}</p>
            </div>
          ))}
        </div>
        <div className="grid items-end gap-4 md:grid-cols-[1fr_1fr_auto]">
          <div className="space-y-2">
            <Label htmlFor="onboarding-target-count">{t("accountPool.onboarding.target.countLabel")}</Label>
            <Input
              id="onboarding-target-count"
              type="number"
              min={0}
              max={100}
              value={value.count}
              onChange={(event) =>
                setDraft({ ...value, count: Math.max(0, Math.min(100, Number(event.target.value) || 0)) })
              }
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="onboarding-target-model">{t("accountPool.onboarding.target.modelLabel")}</Label>
            <Input
              id="onboarding-target-model"
              value={value.model}
              onChange={(event) => setDraft({ ...value, model: event.target.value })}
            />
          </div>
          <Button disabled={busy || query.isLoading || query.isError} onClick={() => void save()}>
            {t("accountPool.onboarding.target.saveButton")}
          </Button>
        </div>
        <Label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(event) => setDraft({ ...value, enabled: event.target.checked })}
          />
          {t("accountPool.onboarding.target.enableAutoQueue")}
        </Label>
        <p className="text-xs text-muted-foreground">{t("accountPool.onboarding.target.footnote")}</p>
      </CardContent>
    </Card>
  );
}
