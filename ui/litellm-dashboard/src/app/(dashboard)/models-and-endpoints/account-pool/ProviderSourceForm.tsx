// 本文件实现 manifest 驱动的通用渠道表单，负责上游校验、模型发现和 Deployment 创建。
import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { CheckCircle2, Loader2, Save } from "lucide-react";
import CreatableModelSelect from "@/components/add_model/CreatableModelSelect";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { ProviderLogo } from "@/components/molecules/models/ProviderLogo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { createPoolAccount, validateProviderService } from "./api";
import type { CreatePoolAccountRequest, ProviderServiceManifest, ProviderValidationResult } from "./types";

interface ProviderSourceFormProps {
  accessToken: string;
  manifests: ProviderServiceManifest[];
  onCreated: () => Promise<void>;
}

interface FormValues {
  providerId: string;
  accountId: string;
  displayName: string;
  apiBase: string;
  apiKey: string;
  group: string;
  maxConcurrency: string;
  models: string[];
}

const capabilityNames: Record<string, string> = {
  connection: "连接校验",
  model_discovery: "模型发现",
  key_listing: "Key 列表",
  account_balance: "账户余额",
  subscriptions: "套餐信息",
  periodic_limits: "周期限额",
  model_pricing: "模型价格",
};

const accountIdPattern = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;

const initialFormValues = (manifest: ProviderServiceManifest): FormValues => ({
  providerId: manifest.provider_id,
  accountId: "",
  displayName: "",
  apiBase: manifest.default_api_base,
  apiKey: "",
  group: "",
  maxConcurrency: "5",
  models: [],
});

export default function ProviderSourceForm({ accessToken, manifests, onCreated }: ProviderSourceFormProps) {
  const firstManifest = manifests[0];
  const [values, setValues] = useState<FormValues>(initialFormValues(firstManifest));
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState<ProviderValidationResult | null>(null);
  const manifest = useMemo(
    () => manifests.find((item) => item.provider_id === values.providerId) ?? firstManifest,
    [firstManifest, manifests, values.providerId],
  );

  const updateIdentity = (fields: Partial<FormValues>) => {
    // URL、Key、分组或服务商发生变化后，旧校验结果不能继续用于保存。
    setValues((current) => ({ ...current, ...fields }));
    setValidation(null);
  };

  const validate = async () => {
    if (!values.apiKey.trim() || !values.apiBase.trim()) {
      NotificationsManager.fromBackend("请填写 API Base URL 和 API Key");
      return;
    }
    setChecking(true);
    try {
      const request = {
        provider_id: values.providerId,
        api_base: values.apiBase.trim(),
        api_key: values.apiKey.trim(),
        group: values.group.trim() || null,
      };
      const result = await validateProviderService(accessToken, request);
      setValidation(result);
      if (!result.ok) {
        NotificationsManager.fromBackend(result.message);
        return;
      }
      setValues((current) => ({
        ...current,
        apiBase: result.normalized_api_base,
        models: result.models.map((item) => item.model),
      }));
      NotificationsManager.success(result.message);
    } catch {
      NotificationsManager.fromBackend("上游校验请求失败，请检查号池服务和网络连接");
    } finally {
      setChecking(false);
    }
  };

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const concurrency = Number(values.maxConcurrency);
    if (!accountIdPattern.test(values.accountId) || !values.displayName.trim()) {
      NotificationsManager.fromBackend("请填写有效的渠道 ID 和显示名称");
      return;
    }
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 10000) {
      NotificationsManager.fromBackend("最大并发必须是 1 到 10000 的整数");
      return;
    }
    if (!validation?.ok || values.models.length === 0) {
      NotificationsManager.fromBackend("请先校验上游渠道，并至少选择或输入一个模型");
      return;
    }

    setSaving(true);
    try {
      const prefix = manifest.litellm_provider_prefix;
      const request: CreatePoolAccountRequest = {
        id: values.accountId,
        display_name: values.displayName.trim(),
        provider: prefix,
        group: values.group.trim() || null,
        base_url_display: values.apiBase,
        enabled: true,
        max_concurrency: concurrency,
        priority: 0,
        weight: 1,
        quotas: { unit: "tokens", total: null, five_hour: null, weekly: null },
        api_key: values.apiKey.trim(),
        deployments: values.models.map((model) => ({ public_model: model, provider_model: `${prefix}/${model}` })),
      };
      const result = await createPoolAccount(accessToken, request);
      if (!result.ok) {
        NotificationsManager.fromBackend(result.message);
        return;
      }
      NotificationsManager.success(result.message);
      setValues((current) => ({
        ...current,
        accountId: "",
        displayName: "",
        apiKey: "",
        group: "",
        models: [],
      }));
      setValidation(null);
      await onCreated();
    } catch {
      NotificationsManager.fromBackend("保存渠道失败，请检查 LiteLLM 管理连接");
    } finally {
      setSaving(false);
    }
  };

  const discoveredModels = validation?.models.map((item) => item.model) ?? [];
  const handleProviderChange = (providerId: string | null) => {
    if (!providerId) return;
    const selected = manifests.find((item) => item.provider_id === providerId);
    updateIdentity({ providerId, apiBase: selected?.default_api_base ?? values.apiBase });
  };

  return (
    <div className="max-w-4xl">
      <div className="mb-5 flex items-center gap-3">
        <ProviderLogo provider={manifest.litellm_provider_prefix} className="h-9 w-9" />
        <div>
          <h3 className="text-base font-semibold">添加上游渠道</h3>
          <p className="text-sm text-muted-foreground">校验官方接口后，将选中的模型创建为 LiteLLM Deployment</p>
        </div>
      </div>

      <form className="space-y-5" onSubmit={save}>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="pool-provider">渠道服务</Label>
            <Select value={values.providerId} disabled={manifests.length < 2} onValueChange={handleProviderChange}>
              <SelectTrigger id="pool-provider" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {manifests.map((item) => (
                  <SelectItem key={item.provider_id} value={item.provider_id}>
                    {item.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Field label="分组" htmlFor="pool-group">
            <Input
              id="pool-group"
              value={values.group}
              placeholder="例如：生产、研发或主账号"
              onChange={(event) => updateIdentity({ group: event.target.value })}
            />
          </Field>
          <Field label="渠道 ID" htmlFor="pool-account-id">
            <Input
              id="pool-account-id"
              value={values.accountId}
              required
              pattern={accountIdPattern.source}
              placeholder="glm-production"
              onChange={(event) => setValues((current) => ({ ...current, accountId: event.target.value }))}
            />
          </Field>
          <Field label="显示名称" htmlFor="pool-display-name">
            <Input
              id="pool-display-name"
              value={values.displayName}
              required
              placeholder="GLM 生产账号"
              onChange={(event) => setValues((current) => ({ ...current, displayName: event.target.value }))}
            />
          </Field>
          <Field label="API Base URL" htmlFor="pool-api-base">
            <Input
              id="pool-api-base"
              type="url"
              value={values.apiBase}
              required
              onChange={(event) => updateIdentity({ apiBase: event.target.value })}
            />
          </Field>
          <Field label="API Key" htmlFor="pool-api-key">
            <Input
              id="pool-api-key"
              type="password"
              value={values.apiKey}
              required
              autoComplete="new-password"
              onChange={(event) => updateIdentity({ apiKey: event.target.value })}
            />
          </Field>
          <Field label="最大并发" htmlFor="pool-max-concurrency">
            <Input
              id="pool-max-concurrency"
              type="number"
              min={1}
              max={10000}
              step={1}
              value={values.maxConcurrency}
              required
              onChange={(event) => setValues((current) => ({ ...current, maxConcurrency: event.target.value }))}
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" disabled={checking} onClick={validate}>
            {checking ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
            校验并获取模型
          </Button>
          {validation?.key_fingerprint && (
            <span className="text-xs text-muted-foreground">Key 指纹：{validation.key_fingerprint}</span>
          )}
        </div>

        {validation && <ValidationSummary validation={validation} />}

        <div className="space-y-2">
          <Label>对外模型</Label>
          <CreatableModelSelect
            value={values.models}
            models={discoveredModels}
            placeholder={validation?.ok ? "选择模型或直接输入模型名" : "请先校验上游渠道"}
            disabled={!validation?.ok}
            testId="account-pool-model-select"
            onChange={(models) => setValues((current) => ({ ...current, models }))}
          />
          <p className="text-xs text-muted-foreground">
            列表来自当前 Key 的官方模型接口；未返回的模型名也可以直接输入并回车。
          </p>
        </div>

        <Button type="submit" disabled={!validation?.ok || saving}>
          {saving ? <Loader2 className="animate-spin" /> : <Save />}
          保存到号池
        </Button>
      </form>
    </div>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

function ValidationSummary({ validation }: { validation: ProviderValidationResult }) {
  const className = validation.ok
    ? "rounded-md border border-emerald-200 bg-emerald-50 p-3"
    : "rounded-md border border-destructive/30 bg-destructive/5 p-3";
  return (
    <div role="status" className={className}>
      <p className="text-sm font-medium">{validation.message}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {validation.capabilities.map((item) => (
          <Badge
            key={item.capability}
            variant={item.state === "supported" ? "secondary" : "outline"}
            title={item.message}
          >
            {capabilityNames[item.capability] ?? item.capability}：{item.state === "supported" ? "支持" : "暂不支持"}
          </Badge>
        ))}
      </div>
    </div>
  );
}
