import { KeyRound } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import type { ProviderServiceManifest, UpstreamProviderManifest } from "../types";

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
          <SelectValue>{providers.find((provider) => provider.provider_id === value)?.display_name}</SelectValue>
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

interface UpstreamProviderSelectProps {
  providers: UpstreamProviderManifest[];
  value: string;
  disabled?: boolean;
  label?: string;
  description?: string;
  onValueChange: (value: string) => void;
}

export function UpstreamProviderSelect({
  providers,
  value,
  disabled,
  label = "上游厂商",
  description,
  onValueChange,
}: UpstreamProviderSelectProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="upstream-provider">{label}</Label>
      <Select value={value} onValueChange={(next) => next && onValueChange(next)} disabled={disabled}>
        <SelectTrigger id="upstream-provider" aria-label={label}>
          <SelectValue placeholder="选择上游厂商">
            {providers.find((provider) => provider.provider_id === value)?.display_name}
          </SelectValue>
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

const forwardingProviders = [
  { provider_id: "openai", display_name: "OpenAI 兼容" },
  { provider_id: "anthropic", display_name: "Anthropic" },
  { provider_id: "gemini", display_name: "Google Gemini" },
  { provider_id: "azure", display_name: "Azure OpenAI" },
  { provider_id: "ollama", display_name: "Ollama" },
  { provider_id: "zai", display_name: "智谱 GLM" },
  { provider_id: "dashscope", display_name: "阿里云百炼" },
  { provider_id: "deepseek", display_name: "DeepSeek" },
  { provider_id: "openrouter", display_name: "OpenRouter" },
  { provider_id: "volcengine", display_name: "火山方舟" },
] as const;

interface ForwardingProviderSelectProps {
  value: string;
  disabled?: boolean;
  onValueChange: (value: string) => void;
}

export function ForwardingProviderSelect({ value, disabled, onValueChange }: ForwardingProviderSelectProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="forwarding-provider">LiteLLM 转发协议</Label>
      <Select value={value} onValueChange={(next) => next && onValueChange(next)} disabled={disabled}>
        <SelectTrigger id="forwarding-provider" aria-label="LiteLLM 转发协议">
          <SelectValue placeholder="选择转发协议">
            {forwardingProviders.find((provider) => provider.provider_id === value)?.display_name}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {forwardingProviders.map((provider) => (
            <SelectItem key={provider.provider_id} value={provider.provider_id}>
              {provider.display_name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-xs text-muted-foreground">该设置只决定 LiteLLM 如何转发已选模型，与上游厂商和解析器独立</p>
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
