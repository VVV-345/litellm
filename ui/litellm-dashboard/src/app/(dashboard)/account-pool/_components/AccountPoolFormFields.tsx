import { KeyRound } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import type { ProviderServiceManifest } from "../types";

interface ProviderProtocolSelectProps {
  providers: ProviderServiceManifest[];
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  description?: string;
}

export function ProviderProtocolSelect({
  providers,
  value,
  onValueChange,
  disabled = false,
  label = "连接协议",
  description,
}: ProviderProtocolSelectProps) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Select
        value={value}
        onValueChange={(nextValue) => nextValue !== null && onValueChange(nextValue)}
        disabled={disabled}
      >
        <SelectTrigger className="w-full" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {providers.map((provider) => (
            <SelectItem key={provider.provider_id} value={provider.provider_id}>
              {provider.display_name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

interface EphemeralCredentialFieldProps {
  id: string;
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  description?: string;
  placeholder?: string;
}

interface EphemeralCredentialInputProps {
  id?: string;
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function EphemeralCredentialInput({
  id,
  value,
  onValueChange,
  disabled = false,
  placeholder,
}: EphemeralCredentialInputProps) {
  return (
    <div className="relative">
      <KeyRound className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
      <Input
        id={id}
        type="password"
        className="pl-9"
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(event) => onValueChange(event.target.value)}
        autoComplete="off"
      />
    </div>
  );
}

export function EphemeralCredentialField({
  id,
  label,
  value,
  onValueChange,
  disabled = false,
  description,
  placeholder,
}: EphemeralCredentialFieldProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <EphemeralCredentialInput
        id={id}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onValueChange={onValueChange}
      />
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}
