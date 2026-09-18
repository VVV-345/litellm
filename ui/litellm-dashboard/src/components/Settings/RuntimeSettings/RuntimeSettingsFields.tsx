/** 本文件提供号池设置页复用的基础数值和开关字段。 */

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

type FieldProps = {
  id: string;
  label: string;
  disabled: boolean;
};

export const NumberSetting = ({
  id,
  label,
  value,
  disabled,
  onChange,
}: FieldProps & { value: number; onChange: (value: number) => void }) => (
  <div className="grid gap-2 rounded-md border p-3">
    <Label htmlFor={id}>{label}</Label>
    <Input
      id={id}
      type="number"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  </div>
);

export const ToggleSetting = ({
  id,
  label,
  checked,
  disabled,
  onChange,
}: FieldProps & { checked: boolean; onChange: (checked: boolean) => void }) => (
  <div className="flex min-h-16 items-center justify-between gap-4 rounded-md border p-3">
    <Label htmlFor={id} className="leading-5">
      {label}
    </Label>
    <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={(value) => onChange(value === true)} />
  </div>
);
