import { formatNumberWithCommas } from "@/utils/dataUtils";

export function CompactNumber({ value, compact = true }: { value: number | null | undefined; compact?: boolean }) {
  const full = formatNumberWithCommas(value ?? 0);
  const abbreviated = compact && Math.abs(value ?? 0) >= 1_000;
  return (
    <span className="tabular-nums" title={full} aria-label={full}>
      {formatNumberWithCommas(value ?? 0, abbreviated ? 1 : 0, abbreviated)}
    </span>
  );
}
