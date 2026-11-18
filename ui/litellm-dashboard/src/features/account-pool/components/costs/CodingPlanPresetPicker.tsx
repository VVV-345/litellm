/** 订阅表单的官方套餐选择与显式套用，不保存费用。 */
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { codingPlanProviders, codingPlansVerifiedOn } from "./codingPlanPresets";
import { money } from "./costForm";

export function CodingPlanPresetPicker({ onApply }: { onApply: (name: string, amount: string, months: 1 | 3 | 12) => void }) {
  const [providerId, setProviderId] = useState("");
  const [planIndex, setPlanIndex] = useState("");
  const provider = codingPlanProviders.find((item) => item.id === providerId);
  const plan = planIndex === "" ? undefined : provider?.plans[Number(planIndex)];
  const cycleMonths = { 月付: 1, 季付: 3, 年付: 12 } as const;
  const selectClass = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm";
  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor="cost-preset-provider">Coding Plan 预设（选填）</Label>
        <span className="text-xs text-muted-foreground">核验 {codingPlansVerifiedOn}</span>
      </div>
      <select
        id="cost-preset-provider"
        className={selectClass}
        value={providerId}
        onChange={(event) => {
          setProviderId(event.target.value);
          setPlanIndex("");
        }}
      >
        <option value="">手动录入 / 选择厂商</option>
        {codingPlanProviders.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
      {provider && (
        <>
          <Label htmlFor="cost-preset-plan">套餐与计费周期</Label>
          <select
            id="cost-preset-plan"
            className={selectClass}
            value={planIndex}
            onChange={(event) => setPlanIndex(event.target.value)}
          >
            <option value="">选择套餐</option>
            {provider.plans.map((item, index) => (
              <option key={`${item.label}-${item.cycle}`} value={index}>
                {item.label} · {item.cycle} · {item.amountCents === null ? "金额待核实" : money(item.amountCents)}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">{provider.note}</p>
          <div className="flex items-center justify-between gap-2">
            <a className="text-xs underline" href={provider.source} target="_blank" rel="noopener noreferrer">
              官方价格来源
            </a>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!plan}
              onClick={() => {
                if (plan)
                  onApply(
                    `${provider.name} ${plan.label}（${plan.cycle}）`,
                    plan.amountCents === null ? "" : (plan.amountCents / 100).toFixed(2),
                    cycleMonths[plan.cycle],
                  );
              }}
            >
              套用套餐
            </Button>
          </div>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        预设为价格快照，金额可修改。服务结束日期按周期预填，可按真实订单修改，点击保存后才计入台账
      </p>
    </div>
  );
}
