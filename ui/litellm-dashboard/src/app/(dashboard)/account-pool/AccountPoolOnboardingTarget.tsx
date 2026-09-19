/** 配置 OAuth 可用数量目标并展示真实缺口，不把待授权计为成功。 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import { listOnboardingTargets, saveOnboardingTarget, type OnboardingTarget } from "./AccountPoolOnboardingApi";

export function AccountPoolOnboardingTarget({
  accessToken,
  supplier,
}: {
  accessToken: string;
  supplier: OnboardingTarget["supplier"];
}) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<OnboardingTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const queryKey = ["account-pool", "onboarding-targets", accessToken];
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
      toast.success("数量目标已保存");
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>可用账号目标</CardTitle>
        <CardDescription>
          仅统计此处导入的 OAuth 账号。数量不足时从待授权库存排队；登录或验证码仍需人工完成，完成验证后才计入可用。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isError && (
          <p role="alert" className="text-destructive">
            目标读取失败，请刷新
          </p>
        )}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            ["已就绪", saved?.ready ?? 0],
            ["处理中", saved?.in_progress ?? 0],
            ["待授权库存", saved?.standby ?? 0],
            ["尚缺", saved?.shortage ?? 0],
          ].map(([label, count]) => (
            <div key={label} className="rounded-xl border bg-muted/30 p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-2 text-2xl font-semibold tabular-nums">{count}</p>
            </div>
          ))}
        </div>
        <div className="grid items-end gap-4 md:grid-cols-[1fr_1fr_auto]">
          <div className="space-y-2">
            <Label htmlFor="onboarding-target-count">目标数量（0～100）</Label>
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
            <Label htmlFor="onboarding-target-model">指定模型（留空不限）</Label>
            <Input
              id="onboarding-target-model"
              value={value.model}
              onChange={(event) => setDraft({ ...value, model: event.target.value })}
            />
          </div>
          <Button disabled={busy || query.isLoading || query.isError} onClick={() => void save()}>
            保存目标
          </Button>
        </div>
        <Label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(event) => setDraft({ ...value, enabled: event.target.checked })}
          />
          启用自动排队补充
        </Label>
        <p className="text-xs text-muted-foreground">
          降低目标不会停用现有卡片。手动暂停的账号不会自动恢复；未准备好邮箱的账号需先确认。
        </p>
      </CardContent>
    </Card>
  );
}
