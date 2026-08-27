// 本文件统一展示号池并发和余额比例等可归一化的运行容量指标。

interface CapacityMeterProps {
  value: number | null;
  label: string;
  tone?: "sky" | "emerald" | "violet";
}

const toneClass: Record<NonNullable<CapacityMeterProps["tone"]>, string> = {
  sky: "bg-sky-500",
  emerald: "bg-emerald-500",
  violet: "bg-violet-500",
};

export default function CapacityMeter({ value, label, tone = "sky" }: CapacityMeterProps) {
  const percent = value === null ? null : Math.min(100, Math.max(0, value * 100));
  return (
    <div className="mt-1.5 min-w-28">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="tabular-nums">{percent === null ? "未知" : `${Math.round(percent)}%`}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full ${toneClass[tone]}`} style={{ width: `${percent ?? 0}%` }} />
      </div>
    </div>
  );
}
