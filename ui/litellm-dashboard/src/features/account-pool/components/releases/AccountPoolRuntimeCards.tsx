/** 展示卡片实际镜像与新卡默认镜像，升级复用发布器的确认和串行任务。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { listRuntimeCards, type ReleaseAction } from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const statusLabels = {
  current: "accountPool.cardImages.alreadyOnTheTargetImage",
  update_available: "accountPool.cardImages.updateAvailable",
  unknown: "accountPool.cardImages.notVerifiedPreflightRunsBeforeUpdating",
  maintenance: "accountPool.cardImages.lastUpgradeIncompleteResumeRequired",
};

export function AccountPoolRuntimeCards({ accessToken, busy, prepare }: {
  accessToken: string;
  busy: boolean;
  prepare: (action: Omit<ReleaseAction, "revision">, description: string) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string[]>([]);
  const query = useQuery({
    queryKey: accountPoolQueryKeys.runtimeCards(accessToken),
    queryFn: () => listRuntimeCards(accessToken),
    enabled: false,
    retry: false,
  });
  const cards = query.data?.cards ?? [];
  const targets = cards.filter((card) => selected.includes(card.environment_id) && card.status !== "current");
  const upgrade = (ids: string[]) => prepare(
    { action: "upgrade_cards", card_ids: ids },
    t("accountPool.cardImages.upgradeOnlyTheSelectedCardsTo", { total: ids.length }),
  );
  return <Card>
    <CardHeader>
      <CardTitle>{t("accountPool.cardImages.cardCpaImages")}</CardTitle>
      <CardDescription>{t("accountPool.cardImages.aSiteVersionChangeDoesNot")}</CardDescription>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="flex gap-2">
        <Button variant="outline" disabled={busy || query.isFetching} onClick={() => void query.refetch()}>{t("accountPool.cardImages.verifyCardImages")}</Button>
        <Button disabled={busy || query.isFetching || query.isError || !targets.length} onClick={() => upgrade(targets.map((card) => card.environment_id))}>{t("accountPool.cardImages.upgradeSelectedCards")}</Button>
      </div>
      {query.isFetching && <p role="status">{t("accountPool.cardImages.readingActualContainerImages")}</p>}
      {query.isError && <p role="alert" className="text-destructive">{query.error.message}</p>}
      {query.data && <p className="break-all text-sm">{t("accountPool.cardImages.defaultImageForNewCards")}<code>{query.data.default_image}</code></p>}
      {cards.map((card) => <div key={card.environment_id} className="flex items-start gap-3 rounded-lg border p-3 text-sm">
        <Checkbox aria-label={t("accountPool.cardImages.selectCard", { name: card.name })} checked={selected.includes(card.environment_id)}
          disabled={busy || query.isFetching || query.isError || card.status === "current"}
          onCheckedChange={(checked) => setSelected((ids) => checked === true ? [...ids, card.environment_id] : ids.filter((id) => id !== card.environment_id))} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium">{card.name} · {t(statusLabels[card.status])}</p>
          <p className="break-all">{t("accountPool.cardImages.currentImageId")}<code>{card.current_image_id ?? t("accountPool.credential.result.unknown")}</code></p>
          <p className="break-all">{t("accountPool.cardImages.targetImageId")}<code>{card.target_image_id ?? t("accountPool.cardImages.notPulledOrUnavailable")}</code></p>
          {card.phase && <p>{t("accountPool.cardImages.checkpoint")}{card.phase}</p>}
        </div>
        <Button variant="outline" disabled={busy || query.isFetching || query.isError || card.status === "current"}
          onClick={() => upgrade([card.environment_id])}>{card.status === "maintenance" ? t("accountPool.cardImages.resumeThisCard") : t("accountPool.cardImages.upgradeThisCard")}</Button>
      </div>)}
    </CardContent>
  </Card>;
}
