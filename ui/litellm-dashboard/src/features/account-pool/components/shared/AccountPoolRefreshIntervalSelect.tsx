/** 复用号池自动刷新档位控件，由调用页面提供文案、当前值和保存行为。 */

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { AccountPoolQuotaRefreshStatus } from "../../api/AccountPoolManagementApi";

type RefreshInterval = AccountPoolQuotaRefreshStatus["interval_minutes"];

export const AccountPoolRefreshIntervalSelect = ({
  value,
  disabled,
  label,
  formatOption,
  onChange,
}: {
  value: RefreshInterval;
  disabled: boolean;
  label: string;
  formatOption: (minutes: RefreshInterval) => string;
  onChange: (minutes: RefreshInterval) => void;
}) => (
  <Select
    value={String(value)}
    onValueChange={(selected) => onChange(Number(selected) as RefreshInterval)}
    disabled={disabled}
  >
    <SelectTrigger className="w-40" aria-label={label}>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      {([5, 15, 30, 60] as const).map((minutes) => (
        <SelectItem key={minutes} value={String(minutes)}>
          {formatOption(minutes)}
        </SelectItem>
      ))}
    </SelectContent>
  </Select>
);
