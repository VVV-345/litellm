/** 展示卡片实际镜像与新卡默认镜像，升级复用发布器的确认和串行任务。 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { listRuntimeCards, type ReleaseAction } from "../../api/AccountPoolReleasesApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

const statusLabels = {
  current: "已是目标镜像，无需更新",
  update_available: "可更新",
  unknown: "尚未核实，更新时先预检",
  maintenance: "上次升级未完成，需要续跑",
};

export function AccountPoolRuntimeCards({ accessToken, busy, prepare }: {
  accessToken: string;
  busy: boolean;
  prepare: (action: Omit<ReleaseAction, "revision">, description: string) => void;
}) {
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
    `仅升级所选 ${ids.length} 张卡到当前已部署版本的 CPA 镜像。相同镜像直接跳过，其余逐张排空、备份和验证，失败停止。保留凭据和启停状态，不部署整站。`,
  );
  return <Card>
    <CardHeader>
      <CardTitle>卡片 CPA 镜像</CardTitle>
      <CardDescription>整站版本变化不代表卡片镜像变化。新建卡片使用下方默认镜像，已有卡片按需升级</CardDescription>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="flex gap-2">
        <Button variant="outline" disabled={busy || query.isFetching} onClick={() => void query.refetch()}>核实卡片镜像</Button>
        <Button disabled={busy || query.isFetching || query.isError || !targets.length} onClick={() => upgrade(targets.map((card) => card.environment_id))}>升级所选卡片</Button>
      </div>
      {query.isFetching && <p role="status">正在读取实际容器镜像…</p>}
      {query.isError && <p role="alert" className="text-destructive">{query.error.message}</p>}
      {query.data && <p className="break-all text-sm">新卡默认镜像：<code>{query.data.default_image}</code></p>}
      {cards.map((card) => <div key={card.environment_id} className="flex items-start gap-3 rounded-lg border p-3 text-sm">
        <Checkbox aria-label={`选择卡片 ${card.name}`} checked={selected.includes(card.environment_id)}
          disabled={busy || query.isFetching || query.isError || card.status === "current"}
          onCheckedChange={(checked) => setSelected((ids) => checked === true ? [...ids, card.environment_id] : ids.filter((id) => id !== card.environment_id))} />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="font-medium">{card.name} · {statusLabels[card.status]}</p>
          <p className="break-all">当前镜像 ID：<code>{card.current_image_id ?? "未知"}</code></p>
          <p className="break-all">目标镜像 ID：<code>{card.target_image_id ?? "尚未拉取或无法读取"}</code></p>
          {card.phase && <p>检查点：{card.phase}</p>}
        </div>
        <Button variant="outline" disabled={busy || query.isFetching || query.isError || card.status === "current"}
          onClick={() => upgrade([card.environment_id])}>{card.status === "maintenance" ? "续跑此卡" : "升级此卡"}</Button>
      </div>)}
    </CardContent>
  </Card>;
}
