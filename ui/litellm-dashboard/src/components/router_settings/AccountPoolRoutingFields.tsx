import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useAccountPoolQuery } from "@/app/(dashboard)/account-pool/useAccountPoolQuery";

export type AccountPoolRoutingValue = {
  selection: "native" | "quota" | "plan" | "expiry" | "ordered";
  preferred_account_ids: string[];
  session_affinity: boolean;
  session_affinity_ttl_seconds: number;
};

export const accountPoolRoutingValue = (value: unknown): AccountPoolRoutingValue => {
  const source = typeof value === "object" && value !== null ? (value as Partial<AccountPoolRoutingValue>) : {};
  return {
    selection: source.selection ?? "native",
    preferred_account_ids: source.preferred_account_ids ?? [],
    session_affinity: source.session_affinity ?? false,
    session_affinity_ttl_seconds: source.session_affinity_ttl_seconds ?? 3600,
  };
};

export function AccountPoolRoutingFields({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (value: AccountPoolRoutingValue) => void;
}) {
  const current = accountPoolRoutingValue(value);
  const { accessToken } = useAuthorized();
  const cards = useAccountPoolQuery(accessToken, current.selection === "ordered", false);
  const available = cards.data ?? [];
  const moveEarlier = (index: number) => {
    const order = [...current.preferred_account_ids];
    [order[index - 1], order[index]] = [order[index], order[index - 1]];
    onChange({ ...current, preferred_account_ids: order });
  };
  return (
    <section className="space-y-4 rounded-xl border bg-muted/20 p-5">
      <div>
        <h3 className="text-sm font-semibold">号池账号选择</h3>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          在有权访问且可用的卡片中选择。保留原生优先级和负载均衡；指定卡片的密钥始终限制在该卡片内。
        </p>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="pool-selection">同优先级卡片偏好</Label>
          <Select
            value={current.selection}
            onValueChange={(selection) =>
              selection && onChange({ ...current, selection: selection as AccountPoolRoutingValue["selection"] })
            }
          >
            <SelectTrigger id="pool-selection" className="w-full">
              <SelectValue>
                {
                  {
                    native: "按原生负载均衡",
                    quota: "优先剩余额度多的账号",
                    plan: "优先高等级套餐",
                    expiry: "优先较早到期的套餐",
                    ordered: "按指定卡片顺序",
                  }[current.selection]
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="native">按原生负载均衡</SelectItem>
              <SelectItem value="quota">优先剩余额度多的账号</SelectItem>
              <SelectItem value="plan">优先高等级套餐</SelectItem>
              <SelectItem value="expiry">优先较早到期的套餐</SelectItem>
              <SelectItem value="ordered">按指定卡片顺序</SelectItem>
            </SelectContent>
          </Select>
          {current.selection === "ordered" && (
            <div className="space-y-2 rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">
                从上到下优先；不可用或本次已失败的卡片会跳过，未列出的卡片最后参与选择。不会扩大密钥权限。
              </p>
              {current.preferred_account_ids.map((id, index) => (
                <div className="flex items-center gap-2 text-xs" key={id}>
                  <span className="min-w-0 flex-1 break-all">
                    {index + 1}. {available.find((card) => card.id === id)?.name ?? id}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={index === 0}
                    onClick={() => moveEarlier(index)}
                  >
                    上移
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      onChange({
                        ...current,
                        preferred_account_ids: current.preferred_account_ids.filter((item) => item !== id),
                      })
                    }
                  >
                    移除
                  </Button>
                </div>
              ))}
              <Select
                value=""
                onValueChange={(id) =>
                  id && onChange({ ...current, preferred_account_ids: [...current.preferred_account_ids, id] })
                }
              >
                <SelectTrigger className="w-full" aria-label="添加优先卡片">
                  <SelectValue placeholder="添加卡片到顺序末尾" />
                </SelectTrigger>
                <SelectContent>
                  {available
                    .filter((card) => !current.preferred_account_ids.includes(card.id))
                    .map((card) => (
                      <SelectItem key={card.id} value={card.id}>
                        {card.name} · {card.enabled_models.length} 个模型 · {card.status}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              {cards.isError && (
                <p role="alert" className="text-xs text-destructive">
                  卡片列表读取失败，请刷新后重试
                </p>
              )}
            </div>
          )}
          <p className="text-xs leading-5 text-muted-foreground">
            使用最近一次额度快照。额度低于单卡阈值、已到期或模型冷却中的卡片会被排除；最终转发前还会检查实时状态。
          </p>
        </div>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="pool-session">同一会话优先使用同一卡片</Label>
            <Switch
              id="pool-session"
              checked={current.session_affinity}
              onCheckedChange={(session_affinity) => onChange({ ...current, session_affinity })}
            />
          </div>
          <Label htmlFor="pool-session-ttl">会话亲和性有效期（秒）</Label>
          <Input
            id="pool-session-ttl"
            type="number"
            min={60}
            max={86400}
            value={current.session_affinity_ttl_seconds}
            disabled={!current.session_affinity}
            onChange={(event) => onChange({ ...current, session_affinity_ttl_seconds: Number(event.target.value) })}
          />
          <p className="text-xs leading-5 text-muted-foreground">
            需要客户端传递会话标识，复用 LiteLLM
            原生亲和性。卡片不可用时，普通新请求可重新选卡；有状态的续接请求不能保证跨账号继续。
          </p>
        </div>
      </div>
    </section>
  );
}
