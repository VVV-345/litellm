/** 本文件创建 OpenAI 兼容号池卡片及多 API Key 凭据，明文只在提交请求期间存在于浏览器内存。 */

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "@/lib/toast";

import { createOpenAICompatibleAccountPoolEnvironment, listAccountPoolProxyProfiles } from "./AccountPoolApi";
import type { AccountPoolProxyProfile } from "./AccountPoolTypes";

interface KeyRow {
  api_key: string;
  proxy_profile_id: string;
  weight: number;
}

interface HeaderRow {
  name: string;
  value: string;
}

export const AccountPoolOpenAICompatibleForm = ({
  accessToken,
  onClose,
  onCreated,
}: {
  accessToken: string | null;
  onClose: () => void;
  onCreated: () => void;
}) => {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [prefix, setPrefix] = useState("");
  const [priority, setPriority] = useState(0);
  const [testModel, setTestModel] = useState("");
  const [customModels, setCustomModels] = useState("");
  const [keys, setKeys] = useState<KeyRow[]>([{ api_key: "", proxy_profile_id: "", weight: 1 }]);
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<AccountPoolProxyProfile[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    void listAccountPoolProxyProfiles(accessToken)
      .then(setProxyProfiles)
      .catch(() => setProxyProfiles([]));
  }, [accessToken]);

  const updateKey = (index: number, key: keyof KeyRow, value: string | number) => {
    setKeys((current) => current.map((item, itemIndex) => (itemIndex === index ? { ...item, [key]: value } : item)));
  };

  const submit = async () => {
    if (!accessToken || !name.trim() || !baseUrl.trim() || !testModel.trim() || keys.some((item) => !item.api_key.trim())) {
      toast.error(t("accountPool.providers.openaiCompatible.required"));
      return;
    }
    setSaving(true);
    try {
      await createOpenAICompatibleAccountPoolEnvironment(accessToken, {
        name: name.trim(),
        provider: "openai",
        channel: "openai_compatible",
        supplier: "openai_compatible",
        provider_family: "openai_compatible",
        openai_compatible: {
          base_url: baseUrl.trim(),
          prefix: prefix.trim(),
          priority,
          test_model: testModel.trim(),
          api_keys: keys.map((item) => ({
            api_key: item.api_key,
            proxy_profile_id: item.proxy_profile_id.trim() || undefined,
            weight: item.weight,
          })),
          headers: headers
            .filter((header) => header.name.trim() && header.value)
            .map((header) => [header.name.trim(), header.value] as [string, string]),
          custom_models: customModels.split(",").map((item) => item.trim()).filter(Boolean),
        },
      });
      toast.success(t("accountPool.providers.openaiCompatible.created"));
      onCreated();
      onClose();
    } catch (error) {
      toast.fromError(error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <Label htmlFor="account-pool-openai-name">{t("accountPool.providers.openaiCompatible.name")}</Label>
        <Input id="account-pool-openai-name" value={name} onChange={(event) => setName(event.target.value)} />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="account-pool-openai-base-url">{t("accountPool.providers.openaiCompatible.baseUrl")}</Label>
        <Input id="account-pool-openai-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="grid gap-2"><Label>{t("accountPool.providers.openaiCompatible.prefix")}</Label><Input value={prefix} onChange={(event) => setPrefix(event.target.value)} /></div>
        <div className="grid gap-2"><Label>{t("accountPool.providers.openaiCompatible.priority")}</Label><Input type="number" value={priority} onChange={(event) => setPriority(Number(event.target.value) || 0)} /></div>
        <div className="grid gap-2"><Label>{t("accountPool.providers.openaiCompatible.testModel")}</Label><Input value={testModel} onChange={(event) => setTestModel(event.target.value)} /></div>
      </div>
      <div className="grid gap-3">
        <div className="flex items-center justify-between gap-3"><Label>{t("accountPool.providers.openaiCompatible.apiKeys")}</Label><Button type="button" size="sm" variant="outline" onClick={() => setKeys((current) => [...current, { api_key: "", proxy_profile_id: "", weight: 1 }])}><Plus />{t("accountPool.providers.openaiCompatible.addKey")}</Button></div>
        {keys.map((item, index) => (
          <div key={`key-${index}`} className="grid gap-2 rounded-md border p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_6rem_auto]">
            <Input aria-label={t("accountPool.providers.openaiCompatible.apiKey", { index: index + 1 })} type="password" value={item.api_key} onChange={(event) => updateKey(index, "api_key", event.target.value)} placeholder="sk-..." />
            <Select value={item.proxy_profile_id || "default"} onValueChange={(value) => updateKey(index, "proxy_profile_id", value === "default" ? "" : value ?? "")}>
              <SelectTrigger aria-label={t("accountPool.providers.openaiCompatible.proxyProfileId")}>
                <SelectValue placeholder={t("accountPool.providers.openaiCompatible.defaultProxy")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">{t("accountPool.providers.openaiCompatible.defaultProxy")}</SelectItem>
                {proxyProfiles.map((profile) => (
                  <SelectItem key={profile.id} value={profile.id}>{profile.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input aria-label={t("accountPool.providers.openaiCompatible.weight")} type="number" min={1} value={item.weight} onChange={(event) => updateKey(index, "weight", Math.max(1, Number(event.target.value) || 1))} />
            <Button type="button" variant="ghost" size="icon-sm" disabled={keys.length === 1} onClick={() => setKeys((current) => current.filter((_, itemIndex) => itemIndex !== index))} aria-label={t("accountPool.providers.openaiCompatible.removeKey")}><Trash2 /></Button>
          </div>
        ))}
      </div>
      <div className="grid gap-3">
        <div className="flex items-center justify-between gap-3">
          <Label>{t("accountPool.providers.openaiCompatible.headers")}</Label>
          <Button type="button" size="sm" variant="outline" onClick={() => setHeaders((current) => [...current, { name: "", value: "" }])}>
            <Plus />{t("accountPool.providers.openaiCompatible.addHeader")}
          </Button>
        </div>
        {headers.map((header, index) => (
          <div key={`header-${index}`} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <Input aria-label={t("accountPool.providers.openaiCompatible.headerName", { index: index + 1 })} value={header.name} onChange={(event) => setHeaders((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item))} placeholder="X-Provider-Feature" />
            <Input aria-label={t("accountPool.providers.openaiCompatible.headerValue", { index: index + 1 })} type="password" value={header.value} onChange={(event) => setHeaders((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} />
            <Button type="button" variant="ghost" size="icon-sm" onClick={() => setHeaders((current) => current.filter((_, itemIndex) => itemIndex !== index))} aria-label={t("accountPool.providers.openaiCompatible.removeHeader")}><Trash2 /></Button>
          </div>
        ))}
      </div>
      <div className="grid gap-2"><Label>{t("accountPool.providers.openaiCompatible.customModels")}</Label><Input value={customModels} onChange={(event) => setCustomModels(event.target.value)} placeholder="model-a, model-b" /></div>
      <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t("accountPool.cancel")}</Button><Button type="button" onClick={() => void submit()} disabled={saving}>{saving ? t("accountPool.create.creating") : t("accountPool.providers.create")}</Button></div>
    </div>
  );
};
