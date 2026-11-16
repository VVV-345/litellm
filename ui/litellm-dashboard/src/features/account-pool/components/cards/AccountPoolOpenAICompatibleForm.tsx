/** 号池标准 API Key 接入：连接、模型发现和保存分别由用户触发。 */
import type { TFunction } from "i18next";
import { ChevronDown, Download, ListFilter, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, extractProxyErrorMessage } from "@/lib/http/client";
import { toast } from "@/lib/toast";

import {
  createOpenAICompatibleAccountPoolEnvironment,
  discoverOpenAICompatibleAccountPoolModels,
  listAccountPoolProxyProfiles,
} from "../../api/AccountPoolApi";
import type { AccountPoolProxyProfile } from "../../utils/AccountPoolTypes";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";

const PRESETS = {
  glm: {
    label: "glmSite",
    hint: "glmStandardHint",
    options: [
      { value: "https://open.bigmodel.cn/api/paas/v4", label: "presetGlmCn" },
      { value: "https://api.z.ai/api/paas/v4", label: "presetGlmGlobal" },
      { value: "https://open.bigmodel.cn/api/coding/paas/v4", label: "presetGlmCodingCn" },
      { value: "https://api.z.ai/api/coding/paas/v4", label: "presetGlmCodingGlobal" },
    ],
  },
  opencode: {
    label: "openCodePlan",
    hint: "openCodeHint",
    options: [
      { value: "https://opencode.ai/inference/openai/v1", label: "presetOpenCodeConsole" },
      { value: "https://opencode.ai/inference/go/openai/v1", label: "presetOpenCodeGo" },
    ],
  },
} as const;
const modelIDs = (value: string) => [
  ...new Set(
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  ),
];
const standardBase = (value: string) => {
  const base = value.trim().replace(/\/+$/, "");
  return /^https?:\/\/[^/?#]+$/i.test(base) ? `${base}/v1` : base;
};

interface HeaderRow {
  name: string;
  value: string;
}

type Discovery =
  | { accessToken: string; state: "loading" }
  | { accessToken: string; state: "error"; message: string }
  | { accessToken: string; state: "ready"; models: string[] };

const discoveryFailure = (error: unknown, t: TFunction) => {
  if (!(error instanceof ApiError) || error.status !== 502) return "";
  const reason = extractProxyErrorMessage(error);
  const status = /^model_list_http_([1-5][0-9]{2})$/.exec(reason)?.[1];
  if (status === "401" || status === "403") {
    return t("accountPool.providers.openaiCompatible.fetchRejected", { status });
  }
  if (status === "404") return t("accountPool.providers.openaiCompatible.fetchNotFound");
  if (status) return t("accountPool.providers.openaiCompatible.fetchHttpError", { status });
  if (reason === "model_list_timeout") return t("accountPool.providers.openaiCompatible.fetchTimedOut");
  if (reason === "model_list_network") return t("accountPool.providers.openaiCompatible.fetchNetworkError");
  if (reason === "model_list_format") return t("accountPool.providers.openaiCompatible.fetchInvalidResponse");
  return "";
};

const discoveryView = (result: Discovery | null, accessToken: string | null) => {
  const source = result?.accessToken === accessToken ? result : null;
  const models = source?.state === "ready" ? source.models : [];
  return {
    models,
    fetching: source?.state === "loading",
    failure: source?.state === "error" ? source.message : null,
    empty: source?.state === "ready" && models.length === 0,
  };
};

export const AccountPoolOpenAICompatibleForm = ({
  mode = "compatible",
  accessToken,
  onClose,
  onCreated,
}: {
  mode?: "compatible" | keyof typeof PRESETS;
  accessToken: string | null;
  onClose: () => void;
  onCreated: () => void;
}) => {
  const { t } = useTranslation();
  const preset = mode === "compatible" ? null : PRESETS[mode];
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState<string>(preset?.options[0].value ?? "");
  const [apiKey, setApiKey] = useState("");
  const [proxyId, setProxyId] = useState("default");
  const [prefix, setPrefix] = useState("");
  const [priority, setPriority] = useState(0);
  const [weight, setWeight] = useState(1);
  const [testModel, setTestModel] = useState("");
  const [customModels, setCustomModels] = useState("");
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<AccountPoolProxyProfile[]>([]);
  const [saving, setSaving] = useState(false);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const discoveryRequest = useRef<AbortController | null>(null);
  const hasConnection = Boolean(baseUrl.trim() && apiKey.trim());

  useEffect(() => {
    if (!accessToken) return;
    let active = true;
    void listAccountPoolProxyProfiles(accessToken)
      .then((profiles) => {
        if (active) setProxyProfiles(profiles);
      })
      .catch(() => {
        if (active) setProxyProfiles([]);
      });
    return () => {
      active = false;
      discoveryRequest.current?.abort();
    };
  }, [accessToken]);

  const clearSource = () => {
    discoveryRequest.current?.abort();
    setDiscovery(null);
  };
  const updateBase = (value: string) => {
    clearSource();
    setBaseUrl(value);
  };
  const updateHeaders = (next: HeaderRow[]) => {
    clearSource();
    setHeaders(next);
  };
  const connection = () => ({
    base_url: standardBase(baseUrl),
    credential: { api_key: apiKey.trim(), proxy_profile_id: proxyId === "default" ? undefined : proxyId, weight },
    headers: headers
      .filter((header) => header.name.trim() && header.value)
      .map((header): [string, string] => [header.name.trim(), header.value]),
  });

  const discoverModels = async () => {
    if (!accessToken || !hasConnection) return;
    clearSource();
    const controller = new AbortController();
    discoveryRequest.current = controller;
    setDiscovery({ accessToken, state: "loading" });
    try {
      const models = await discoverOpenAICompatibleAccountPoolModels(accessToken, connection(), controller.signal);
      if (controller.signal.aborted) return;
      setDiscovery({ accessToken, state: "ready", models });
    } catch (error) {
      if (!controller.signal.aborted) {
        setDiscovery({ accessToken, state: "error", message: discoveryFailure(error, t) });
      }
    }
  };

  const submit = async () => {
    const hasCardDetails = Boolean(name.trim() && testModel.trim());
    if (!accessToken || !hasConnection || !hasCardDetails) {
      toast.error(t("accountPool.providers.openaiCompatible.required"));
      return;
    }
    const { base_url, credential, headers: configuredHeaders } = connection();
    const configuredModels = modelIDs(customModels);
    const request = {
      name: name.trim(),
      provider: "openai",
      channel: "openai_compatible",
      supplier: "openai_compatible",
      provider_family: "openai_compatible",
      openai_compatible: {
        base_url,
        prefix: prefix.trim(),
        priority,
        test_model: testModel.trim(),
        api_keys: [credential],
        headers: configuredHeaders,
        custom_models: preset && !configuredModels.length ? [testModel.trim()] : configuredModels,
      },
    } satisfies Parameters<typeof createOpenAICompatibleAccountPoolEnvironment>[1];
    setSaving(true);
    try {
      await createOpenAICompatibleAccountPoolEnvironment(accessToken, request);
      toast.success(t("accountPool.providers.openaiCompatible.created"));
      onCreated();
      onClose();
    } catch (error) {
      toast.fromError(error);
    } finally {
      setSaving(false);
    }
  };

  const sites = preset?.options.map(({ value, label }) => ({
    value,
    label: t(`accountPool.providers.openaiCompatible.${label}`),
  }));
  const proxies = [
    { value: "default", label: t("accountPool.providers.openaiCompatible.defaultProxy") },
    ...proxyProfiles.map(({ id, name }) => ({ value: id, label: name })),
  ];
  const source = discoveryView(discovery, accessToken);
  const selected = modelIDs(customModels);
  const candidates = source.models.filter((model) => !selected.includes(model));
  const testOptions = [...new Set([...selected, ...source.models])];
  const canFetch = Boolean(accessToken && hasConnection && !source.fetching);
  const selectModel = (model: string | null) => {
    if (!model) return;
    setCustomModels([...selected, model].join(", "));
    if (!testModel.trim()) setTestModel(model);
  };

  return (
    <div className="grid gap-4">
      <div className="grid items-start gap-4 md:grid-cols-2">
        <section className="grid min-w-0 gap-4 rounded-xl border bg-muted/15 p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <AccountPoolSupplierLogo supplier={mode === "compatible" ? "openai_compatible" : mode} className="size-5" />
            {t("accountPool.providers.openaiCompatible.connection")}
          </h3>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-name">{t("accountPool.providers.openaiCompatible.name")}</Label>
            <Input
              id="account-pool-openai-name"
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          {preset && sites && (
            <div className="grid gap-2">
              <Label htmlFor="account-pool-compatible-preset">
                {t(`accountPool.providers.openaiCompatible.${preset.label}`)}
              </Label>
              <Select
                value={baseUrl}
                items={sites}
                onValueChange={(value) => {
                  if (value) updateBase(value);
                }}
              >
                <SelectTrigger id="account-pool-compatible-preset" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sites.map(({ value, label }) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {t(`accountPool.providers.openaiCompatible.${preset.hint}`)}
              </p>
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-base-url">{t("accountPool.providers.openaiCompatible.baseUrl")}</Label>
            <Input
              id="account-pool-openai-base-url"
              value={baseUrl}
              readOnly={preset !== null}
              placeholder="https://api.example.com"
              onChange={(event) => updateBase(event.target.value)}
            />
            {mode === "compatible" && (
              <p className="text-xs text-muted-foreground">{t("accountPool.providers.openaiCompatible.baseUrlHint")}</p>
            )}
          </div>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-key">{t("accountPool.providers.openaiCompatible.apiKeys")}</Label>
            <Input
              id="account-pool-openai-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => {
                clearSource();
                setApiKey(event.target.value);
              }}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-proxy">
              {t("accountPool.providers.openaiCompatible.proxyProfileId")}
            </Label>
            <Select
              items={proxies}
              value={proxyId}
              onValueChange={(value) => {
                clearSource();
                setProxyId(value ?? "default");
              }}
            >
              <SelectTrigger id="account-pool-openai-proxy" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {proxies.map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </section>
        <section className="grid min-w-0 gap-4 rounded-xl border bg-muted/15 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <ListFilter className="size-4 text-primary" />
              {t("accountPool.providers.openaiCompatible.models")}
            </h3>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={saving || !canFetch}
              onClick={() => void discoverModels()}
            >
              <Download className="size-4" />
              {t(
                `accountPool.providers.openaiCompatible.${source.fetching ? "fetchingModels" : "fetchModels"}`,
              )}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t("accountPool.providers.openaiCompatible.fetchModelsHint")}</p>
          {source.failure !== null && (
            <p role="alert" className="text-sm text-destructive">
              {source.failure ? source.failure + " " : ""}
              {t("accountPool.providers.openaiCompatible.fetchFailed")}
            </p>
          )}
          {source.empty && (
            <p role="status" className="text-sm text-muted-foreground">
              {t("accountPool.providers.openaiCompatible.noModels")}
            </p>
          )}
          {candidates.length > 0 && (
            <Combobox items={candidates} value={null} onValueChange={selectModel}>
              <ComboboxInput
                aria-label={t("accountPool.providers.openaiCompatible.addSourceModel")}
                placeholder={t("accountPool.providers.openaiCompatible.addSourceModel")}
              />
              <ComboboxContent>
                <ComboboxEmpty>{t("accountPool.providers.openaiCompatible.noMatchingModels")}</ComboboxEmpty>
                <ComboboxList>
                  {(model: string) => (
                    <ComboboxItem key={model} value={model}>
                      {model}
                    </ComboboxItem>
                  )}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          )}
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-custom-models">
              {t("accountPool.providers.openaiCompatible.customModels")}
            </Label>
            <Input
              id="account-pool-openai-custom-models"
              value={customModels}
              placeholder="model-a, model-b"
              onChange={(event) => setCustomModels(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-openai-test-model">
              {t("accountPool.providers.openaiCompatible.testModel")}
            </Label>
            <Input
              id="account-pool-openai-test-model"
              value={testModel}
              list="account-pool-openai-test-options"
              onChange={(event) => setTestModel(event.target.value)}
            />
            <datalist id="account-pool-openai-test-options">
              {testOptions.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </div>
        </section>
      </div>
      <details className="group rounded-xl border">
        <summary className="flex cursor-pointer items-center justify-between gap-2 p-3 text-sm font-medium">
          {t("accountPool.providers.openaiCompatible.advanced")}
          <ChevronDown className="size-4 text-muted-foreground group-open:rotate-180" />
        </summary>
        <div className="grid gap-3 border-t p-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Label className="grid gap-2">
              {t("accountPool.providers.openaiCompatible.prefix")}
              <Input value={prefix} onChange={(event) => setPrefix(event.target.value)} />
            </Label>
            <Label className="grid gap-2">
              {t("accountPool.providers.openaiCompatible.priority")}
              <Input
                type="number"
                value={priority}
                onChange={(event) => setPriority(Number(event.target.value) || 0)}
              />
            </Label>
            <Label className="grid gap-2">
              {t("accountPool.providers.openaiCompatible.weight")}
              <Input
                type="number"
                min={1}
                value={weight}
                onChange={(event) => setWeight(Math.max(1, Number(event.target.value) || 1))}
              />
            </Label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">{t("accountPool.providers.openaiCompatible.headers")}</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setHeaders([...headers, { name: "", value: "" }])}
            >
              <Plus />
              {t("accountPool.providers.openaiCompatible.addHeader")}
            </Button>
          </div>
          {headers.map((header, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <Input
                aria-label={t("accountPool.providers.openaiCompatible.headerName", { index: index + 1 })}
                value={header.name}
                placeholder="X-Provider-Feature"
                onChange={(event) =>
                  updateHeaders(headers.map((item, i) => (i === index ? { ...item, name: event.target.value } : item)))
                }
              />
              <Input
                aria-label={t("accountPool.providers.openaiCompatible.headerValue", { index: index + 1 })}
                type="password"
                value={header.value}
                onChange={(event) =>
                  updateHeaders(headers.map((item, i) => (i === index ? { ...item, value: event.target.value } : item)))
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => updateHeaders(headers.filter((_, i) => i !== index))}
                aria-label={t("accountPool.providers.openaiCompatible.removeHeader")}
              >
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      </details>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
          {t("accountPool.cancel")}
        </Button>
        <Button type="button" onClick={() => void submit()} disabled={saving}>
          {saving ? t("accountPool.create.creating") : t("accountPool.providers.create")}
        </Button>
      </div>
    </div>
  );
};
