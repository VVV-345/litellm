import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

export type AccountPoolRoutingValue = {
  selection: "native" | "quota" | "plan" | "expiry";
  session_affinity: boolean;
  session_affinity_ttl_seconds: number;
};

export const accountPoolRoutingValue = (value: unknown): AccountPoolRoutingValue => {
  const source = typeof value === "object" && value !== null ? (value as Partial<AccountPoolRoutingValue>) : {};
  return {
    selection: source.selection ?? "native",
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
                  }[current.selection]
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="native">按原生负载均衡</SelectItem>
              <SelectItem value="quota">优先剩余额度多的账号</SelectItem>
              <SelectItem value="plan">优先高等级套餐</SelectItem>
              <SelectItem value="expiry">优先较早到期的套餐</SelectItem>
            </SelectContent>
          </Select>
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
