"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, Link2, Loader2, Plus, RefreshCw, Settings2, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import CreatableModelSelect from "@/components/add_model/CreatableModelSelect";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import {
  accountPoolKeys,
  createChannel,
  getChannel,
  importChannel,
  updateChannel,
  validateProviderService,
} from "../api";
import {
  buildDiscoveredBindings,
  canSubmitCreateSelection,
  initialModelSelection,
  validateDiscoveryResult,
} from "../channelModelSelection";
import { channelPriorityPresentation, parseOptionalNumber } from "../accountPoolPresentation";
import { MultiSelect } from "@/components/shared/MultiSelect";
import { EphemeralCredentialField, ProviderProtocolSelect } from "./AccountPoolFormFields";
import { AccountPoolQueryState } from "./AccountPoolPanel";
import { providerModelForSelectedModel } from "./providerModel";
import type {
  AdministrativeState,
  ChannelBindingInput,
  ChannelDetail,
  ChannelMutationRequest,
  ChannelOperation,
  ChannelPriority,
  ChannelSummary,
  ProviderServiceManifest,
  QuotaConfig,
} from "../types";

type ChannelFormMode = "create" | "edit" | "import";
type ModelSelectionSnapshot = {
  capabilitySignature: string;
  revision: number;
  selection: ReturnType<typeof initialModelSelection>;
};
type ModelSelectionResolution = {
  manualMapping: boolean;
  snapshot: ModelSelectionSnapshot | null;
  capabilitySignature: string;
  revision: number;
  provider: ProviderServiceManifest | undefined;
};

interface ChannelFormDialogProps {
  accessToken: string;
  mode: ChannelFormMode;
  channel: ChannelSummary | null;
  providers: ProviderServiceManifest[];
  knownModels: string[];
  onClose: () => void;
  onAccepted: (operation: ChannelOperation) => Promise<void>;
}

const emptyQuotas: QuotaConfig = { unit: "tokens", total: null, five_hour: null, weekly: null };
const priorityOptions: ReadonlyArray<{ label: string; value: ChannelPriority }> = [
  { label: "最高", value: 400 },
  { label: "高", value: 300 },
  { label: "中", value: 200 },
  { label: "低", value: 100 },
];
const emptyBinding = (ownership: ChannelBindingInput["ownership"]): ChannelBindingInput => ({
  binding_id: null,
  public_model: "",
  provider_model: null,
  litellm_deployment_id: null,
  ownership,
  enabled: true,
});
const preserveDiscoveredBindings = (
  models: string[],
  providerPrefix: string,
  currentBindings: ChannelBindingInput[],
): ChannelBindingInput[] =>
  buildDiscoveredBindings(models, providerPrefix).map((binding) => {
    const existing = currentBindings.find((candidate) => candidate.public_model === binding.public_model);
    return existing ? { ...existing, provider_model: binding.provider_model } : binding;
  });
const defaultOwnership = (mode: ChannelFormMode): ChannelBindingInput["ownership"] =>
  mode === "import" ? "externally_managed" : "pool_managed";
const dialogTitle = (mode: ChannelFormMode): string => {
  if (mode === "import") return "导入已有 Deployment";
  if (mode === "edit") return "编辑渠道";
  return "创建渠道";
};
const successMessage = (mode: ChannelFormMode): string => {
  if (mode === "import") return "渠道导入已提交";
  if (mode === "edit") return "渠道更新已提交";
  return "渠道创建已提交";
};
const submitLabel = (mode: ChannelFormMode): string => {
  if (mode === "import") return "导入";
  if (mode === "edit") return "保存";
  return "创建";
};
const resolveModelSelection = ({
  manualMapping,
  snapshot,
  capabilitySignature,
  revision,
  provider,
}: ModelSelectionResolution) => {
  if (manualMapping) return { kind: "manual" as const };
  if (snapshot?.capabilitySignature === capabilitySignature && snapshot.revision === revision)
    return snapshot.selection;
  return initialModelSelection(provider);
};

export default function ChannelFormDialog({
  accessToken,
  mode,
  channel,
  providers,
  knownModels,
  onClose,
  onAccepted,
}: ChannelFormDialogProps) {
  const editing = mode === "edit";
  const detailQuery = useQuery({
    queryKey: accountPoolKeys.channel(channel?.channel_id ?? "new"),
    queryFn: () => getChannel(accessToken, channel!.channel_id),
    enabled: editing && channel !== null,
  });

  if (editing && !detailQuery.data) {
    return (
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>编辑渠道</DialogTitle>
            <DialogDescription>正在读取 PostgreSQL 中的完整渠道配置</DialogDescription>
          </DialogHeader>
          <AccountPoolQueryState
            kind={detailQuery.isError ? "error" : "loading"}
            message={detailQuery.isError ? "读取渠道详情失败，请关闭后重试" : "正在读取渠道详情"}
            className="min-h-48"
          />
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <ChannelFormContent
      accessToken={accessToken}
      mode={mode}
      channel={channel}
      providers={providers}
      knownModels={knownModels}
      onClose={onClose}
      onAccepted={onAccepted}
      initial={detailQuery.data ?? null}
    />
  );
}

function ChannelFormContent({
  accessToken,
  mode,
  channel,
  providers,
  knownModels,
  onClose,
  onAccepted,
  initial,
}: ChannelFormDialogProps & { initial: ChannelDetail | null }) {
  const editing = mode === "edit";
  const importing = mode === "import";
  const preferredProvider = providers.find((item) => item.provider_id === "openai_compatible") ?? providers[0];
  const initialProvider =
    initial?.provider ?? channel?.provider ?? preferredProvider?.provider_id ?? "openai_compatible";
  const [displayName, setDisplayName] = useState(initial?.display_name ?? channel?.display_name ?? "");
  const [provider, setProvider] = useState(initialProvider);
  const [group, setGroup] = useState(initial?.group ?? channel?.group ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url_display ?? channel?.base_url_display ?? "");
  const [administrativeState, setAdministrativeState] = useState<AdministrativeState>(
    initial?.administrative_state ?? channel?.administrative_state ?? "enabled",
  );
  const [maxConcurrency, setMaxConcurrency] = useState(
    String(initial?.max_concurrency ?? channel?.max_concurrency ?? 1),
  );
  const [priority, setPriority] = useState<ChannelPriority>(initial?.priority ?? channel?.priority ?? 200);
  const [weight, setWeight] = useState(String(initial?.weight ?? channel?.weight ?? 1));
  const [quotas, setQuotas] = useState<QuotaConfig>(initial?.quotas ?? emptyQuotas);
  const [apiKey, setApiKey] = useState("");
  const [bindings, setBindings] = useState<ChannelBindingInput[]>(
    initial?.bindings ?? (mode === "create" ? [] : [emptyBinding(defaultOwnership(mode))]),
  );
  const selectedProvider = providers.find((item) => item.provider_id === provider);
  const discoveryCapabilityState = selectedProvider?.capabilities.find(
    (capability) => capability.capability === "model_discovery",
  )?.state;
  const discoveryCapabilitySignature = `${provider}:${discoveryCapabilityState ?? "missing"}`;
  const [manualMapping, setManualMapping] = useState(false);
  const [discoveryRevision, setDiscoveryRevision] = useState(0);
  const [discoverySelection, setDiscoverySelection] = useState<ModelSelectionSnapshot | null>(null);
  const modelSelectionInput: ModelSelectionResolution = {
    manualMapping,
    snapshot: discoverySelection,
    capabilitySignature: discoveryCapabilitySignature,
    revision: discoveryRevision,
    provider: selectedProvider,
  };
  const modelSelection = resolveModelSelection(modelSelectionInput);

  const invalidateDiscovery = () => {
    if (importing || manualMapping) return;
    setDiscoveryRevision((current) => current + 1);
    if (!editing) setBindings([]);
  };

  const close = () => {
    setApiKey("");
    onClose();
  };

  const request = (): ChannelMutationRequest => ({
    display_name: displayName.trim(),
    provider,
    group: group.trim() || null,
    base_url_display: baseUrl.trim(),
    administrative_state: administrativeState,
    max_concurrency: Number(maxConcurrency),
    priority,
    weight: Number(weight),
    quotas,
    api_key: apiKey || null,
    bindings,
  });

  const mutation = useMutation({
    mutationFn: () => {
      const payload = request();
      if (mode === "create") return createChannel(accessToken, payload);
      if (mode === "import") return importChannel(accessToken, payload);
      return updateChannel(accessToken, channel!.channel_id, payload);
    },
    onSuccess: async (operation) => {
      NotificationsManager.success(successMessage(mode));
      await onAccepted(operation);
      close();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
    onSettled: () => setApiKey(""),
  });
  const discoveryMutation = useMutation({
    mutationFn: () => {
      const snapshot = {
        provider_id: provider,
        api_base: baseUrl.trim(),
        api_key: apiKey,
        group: group.trim() || null,
      };
      return validateProviderService(accessToken, snapshot).then((result) => ({ result, snapshot }));
    },
    onSuccess: ({ result, snapshot }) => {
      const inputsMatch =
        snapshot.provider_id === provider &&
        snapshot.api_base === baseUrl.trim() &&
        snapshot.api_key === apiKey &&
        snapshot.group === (group.trim() || null);
      if (!inputsMatch) return;
      const discoveredProvider = providers.find((item) => item.provider_id === snapshot.provider_id);
      const selection = validateDiscoveryResult(initialModelSelection(discoveredProvider), result);
      setDiscoverySelection({
        capabilitySignature: discoveryCapabilitySignature,
        revision: discoveryRevision,
        selection,
      });
      if (selection.kind !== "discovered") return;
      setManualMapping(false);
      const providerPrefix = discoveredProvider?.litellm_provider_prefix ?? "";
      setBindings(preserveDiscoveredBindings(selection.models, providerPrefix, bindings));
    },
    onError: () =>
      setDiscoverySelection({
        capabilitySignature: discoveryCapabilitySignature,
        revision: discoveryRevision,
        selection: { kind: "manual-required", reason: "validation-failed" },
      }),
  });

  const bindingsValid =
    bindings.length > 0 &&
    bindings.every((binding) => {
      const baseValid = Boolean(
        binding.public_model.trim() && (binding.provider_model?.trim() || binding.litellm_deployment_id),
      );
      return importing ? baseValid && Boolean(binding.litellm_deployment_id) : baseValid;
    });
  const canSubmit =
    !mutation.isPending &&
    Boolean(displayName.trim() && provider && baseUrl.trim()) &&
    Number(maxConcurrency) >= 1 &&
    Number(weight) >= 1 &&
    Number(weight) <= 100 &&
    bindingsValid &&
    (mode !== "create" || canSubmitCreateSelection(modelSelection, bindings));
  const inputsDisabled = discoveryMutation.isPending;
  const canDiscoverModels = !importing && discoveryCapabilityState === "supported";
  const showingDiscoveredModels = modelSelection.kind === "discovered";
  const showManualBindings =
    importing || manualMapping || modelSelection.kind === "manual" || (editing && !showingDiscoveredModels);

  const selectedModels = useMemo(() => bindings.map((binding) => binding.public_model).filter(Boolean), [bindings]);
  const setModels = (models: string[]) => {
    setBindings(
      models.map((model) => {
        const existing = bindings.find((binding) => binding.public_model === model);
        if (existing) return existing;
        const providerPrefix = selectedProvider?.litellm_provider_prefix ?? "";
        return {
          ...emptyBinding(defaultOwnership(mode)),
          public_model: model,
          provider_model: providerModelForSelectedModel(model, providerPrefix),
        };
      }),
    );
  };
  const updateBinding = (index: number, patch: Partial<ChannelBindingInput>) =>
    setBindings(bindings.map((binding, bindingIndex) => (bindingIndex === index ? { ...binding, ...patch } : binding)));
  const chooseProvider = (value: string | null) => {
    if (!value) return;
    const manifest = providers.find((item) => item.provider_id === value);
    if (value !== provider) setApiKey("");
    setProvider(value);
    if (manifest?.default_api_base) setBaseUrl(manifest.default_api_base);
    invalidateDiscovery();
  };
  const setDiscoveredModels = (models: string[]) => {
    if (modelSelection.kind !== "discovered") return;
    const providerPrefix = selectedProvider?.litellm_provider_prefix ?? "";
    setBindings(
      preserveDiscoveredBindings(
        models.filter((model) => modelSelection.models.includes(model)),
        providerPrefix,
        bindings,
      ),
    );
  };
  const manualFallback = () => {
    setManualMapping(true);
    setBindings((currentBindings) =>
      currentBindings.length > 0 ? currentBindings : [emptyBinding(defaultOwnership(mode))],
    );
  };

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{dialogTitle(mode)}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Key 留空不会轮换；输入 Key 获取模型后，保存时会一并更新渠道 Key"
              : "Key 只随本次请求提交，结束后立即从组件状态清除"}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <section className="grid gap-4 rounded-md border p-4">
            <div className="flex items-center gap-2">
              <Settings2 className="size-4 text-muted-foreground" />
              <div>
                <h3 className="text-sm font-medium">基本信息</h3>
                <p className="text-xs text-muted-foreground">用于识别渠道并控制账户池调度</p>
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="channel-name">显示名称</Label>
              <Input id="channel-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
            </div>
          </section>

          <section className="grid gap-4 rounded-md border p-4">
            <div className="flex items-center gap-2">
              <Link2 className="size-4 text-muted-foreground" />
              <div>
                <h3 className="text-sm font-medium">资源侧接入</h3>
                <p className="text-xs text-muted-foreground">选择服务商协议后，填写地址和用于读取模型列表的凭据</p>
              </div>
            </div>
            <ProviderProtocolSelect
              providers={providers}
              value={provider}
              disabled={inputsDisabled}
              label="服务商 / 接口协议"
              description="该选择只决定鉴权和资源侧模型列表的请求方式；渠道数据解析由独立解析器处理"
              onValueChange={chooseProvider}
            />
            <div className="grid gap-2">
              <Label htmlFor="channel-url">API 地址</Label>
              <Input
                id="channel-url"
                value={baseUrl}
                placeholder="例如：https://gateway.example/v1"
                disabled={inputsDisabled}
                onChange={(event) => {
                  setBaseUrl(event.target.value);
                  invalidateDiscovery();
                }}
              />
              <p className="text-xs text-muted-foreground">填写 API 基础地址，不要追加 /models</p>
            </div>
            <EphemeralCredentialField
              id="channel-key"
              label={editing ? "用于获取模型的新 Key（可选）" : "接入凭据"}
              value={apiKey}
              disabled={inputsDisabled}
              description={
                editing
                  ? "输入后可获取资源侧模型；保存时会更新渠道 Key，凭据不会保留在浏览器状态中"
                  : "凭据仅随本次请求提交，不会保留在浏览器状态中"
              }
              onValueChange={(value) => {
                setApiKey(value);
                invalidateDiscovery();
              }}
            />
          </section>

          <section className="grid gap-4 rounded-md border p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-medium">模型与分组</h3>
                <p className="text-xs text-muted-foreground">
                  {canDiscoverModels ? "从资源侧读取当前凭据可访问的模型" : "可选择或输入公共模型名"}
                </p>
              </div>
              {showManualBindings && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setBindings([...bindings, emptyBinding(defaultOwnership(mode))])}
                  disabled={inputsDisabled}
                >
                  <Plus />
                  添加映射
                </Button>
              )}
            </div>

            {canDiscoverModels && (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => discoveryMutation.mutate()}
                  disabled={!provider || !baseUrl.trim() || !apiKey || discoveryMutation.isPending}
                >
                  {discoveryMutation.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                  从资源侧获取
                </Button>
                {modelSelection.kind === "discovered" && (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setDiscoveredModels(modelSelection.models)}
                      disabled={selectedModels.length === modelSelection.models.length}
                    >
                      全部选择
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => setDiscoveredModels([])}
                      disabled={selectedModels.length === 0}
                    >
                      清除全部
                    </Button>
                  </>
                )}
              </div>
            )}

            {!importing && modelSelection.kind === "ready-to-validate" && (
              <p className="text-sm text-muted-foreground">
                {editing
                  ? "输入用于读取模型的 Key 后，可从资源侧获取模型；未获取前会保留现有映射"
                  : "填写 API 地址和接入凭据后，一键获取资源侧模型"}
              </p>
            )}
            {!importing && showingDiscoveredModels && (
              <>
                <p className="text-sm text-emerald-700">已获取 {modelSelection.models.length} 个模型，默认全部选中</p>
                <MultiSelect
                  options={modelSelection.models.map((model) => ({ label: model, value: model }))}
                  value={selectedModels}
                  placeholder="选择资源侧模型"
                  disabled={inputsDisabled}
                  onValueChange={setDiscoveredModels}
                />
                <Button type="button" variant="link" className="w-fit px-0" onClick={manualFallback}>
                  改用手动映射
                </Button>
              </>
            )}
            {!importing && modelSelection.kind === "manual-required" && (
              <div className="grid gap-2 text-sm text-muted-foreground">
                <p>{modelSelection.message || "当前连接协议无法自动获取模型，请检查设置或改用手动映射"}</p>
                {modelSelection.failureCode && <p>错误代码: {modelSelection.failureCode}</p>}
                <Button type="button" variant="outline" className="w-fit" onClick={manualFallback}>
                  使用手动映射
                </Button>
              </div>
            )}
            {showManualBindings && (
              <>
                <CreatableModelSelect
                  value={selectedModels}
                  models={mode === "create" ? [] : knownModels}
                  placeholder="选择或输入模型"
                  disabled={inputsDisabled}
                  onChange={setModels}
                  testId="channel-model-select"
                />
                {bindings.map((binding, index) => (
                  <div
                    key={binding.binding_id ?? index}
                    className="grid gap-2 rounded-md bg-muted/40 p-3 sm:grid-cols-[1fr_1fr_auto]"
                  >
                    <div className="grid gap-1">
                      <Label htmlFor={`binding-public-${index}`}>公共模型</Label>
                      <Input
                        id={`binding-public-${index}`}
                        value={binding.public_model}
                        disabled={inputsDisabled}
                        onChange={(event) => updateBinding(index, { public_model: event.target.value })}
                      />
                    </div>
                    <div className="grid gap-1">
                      <Label htmlFor={`binding-provider-${index}`}>资源侧模型</Label>
                      <Input
                        id={`binding-provider-${index}`}
                        value={binding.provider_model ?? ""}
                        disabled={inputsDisabled}
                        onChange={(event) => updateBinding(index, { provider_model: event.target.value || null })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="self-end"
                      disabled={inputsDisabled}
                      onClick={() => setBindings(bindings.filter((_, bindingIndex) => bindingIndex !== index))}
                    >
                      <Trash2 />
                      <span className="sr-only">删除绑定</span>
                    </Button>
                    {(importing || binding.ownership === "externally_managed") && (
                      <div className="grid gap-1 sm:col-span-2">
                        <Label>LiteLLM Deployment ID</Label>
                        <Input
                          value={binding.litellm_deployment_id ?? ""}
                          disabled={inputsDisabled}
                          onChange={(event) =>
                            updateBinding(index, {
                              litellm_deployment_id: event.target.value || null,
                              ownership: "externally_managed",
                            })
                          }
                        />
                      </div>
                    )}
                    <label className="flex items-center gap-2 self-end text-sm">
                      <Checkbox
                        checked={binding.enabled}
                        disabled={inputsDisabled}
                        onCheckedChange={(checked) => updateBinding(index, { enabled: checked })}
                      />
                      启用
                    </label>
                  </div>
                ))}
              </>
            )}

            <div className="grid gap-2 border-t pt-4">
              <Label htmlFor="channel-group">分组（可选）</Label>
              <Input
                id="channel-group"
                value={group}
                disabled={inputsDisabled}
                onChange={(event) => {
                  setGroup(event.target.value);
                  invalidateDiscovery();
                }}
              />
            </div>
          </section>

          <Collapsible defaultOpen={mode !== "create"} className="rounded-md border">
            <CollapsibleTrigger className="flex w-full items-center justify-between gap-3 p-4 text-left">
              <div>
                <h3 className="text-sm font-medium">高级设置</h3>
                <p className="text-xs text-muted-foreground">调度参数和额度</p>
              </div>
              <ChevronDown className="size-4 text-muted-foreground" />
            </CollapsibleTrigger>
            <CollapsibleContent className="grid gap-4 border-t p-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="grid gap-2">
                  <Label>状态</Label>
                  <Select
                    value={administrativeState}
                    onValueChange={(value) => value && setAdministrativeState(value as AdministrativeState)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="enabled">启用</SelectItem>
                      <SelectItem value="paused">暂停</SelectItem>
                      <SelectItem value="disabled">停用</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="channel-concurrency">最大并发</Label>
                  <Input
                    id="channel-concurrency"
                    type="number"
                    min={1}
                    value={maxConcurrency}
                    onChange={(event) => setMaxConcurrency(event.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="channel-priority">优先级</Label>
                  <Select
                    value={String(priority)}
                    onValueChange={(value) => value && setPriority(Number(value) as ChannelPriority)}
                  >
                    <SelectTrigger id="channel-priority">
                      <SelectValue>{channelPriorityPresentation[priority].label}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {priorityOptions.map((option) => (
                        <SelectItem key={option.value} value={String(option.value)}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="channel-weight">权重</Label>
                  <Input
                    id="channel-weight"
                    type="number"
                    min={1}
                    max={100}
                    value={weight}
                    onChange={(event) => setWeight(event.target.value)}
                  />
                </div>
              </div>
              <div className="grid gap-3 border-t pt-4">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium">额度（可选）</p>
                  <Select
                    value={quotas.unit}
                    onValueChange={(value) => value && setQuotas({ ...quotas, unit: value as QuotaConfig["unit"] })}
                  >
                    <SelectTrigger className="w-28">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="tokens">tokens</SelectItem>
                      <SelectItem value="usd">USD</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="grid gap-1">
                    <Label>总额</Label>
                    <Input
                      type="number"
                      min={0}
                      value={quotas.total ?? ""}
                      onChange={(event) => setQuotas({ ...quotas, total: parseOptionalNumber(event.target.value) })}
                    />
                  </div>
                  <div className="grid gap-1">
                    <Label>5 小时</Label>
                    <Input
                      type="number"
                      min={0}
                      value={quotas.five_hour ?? ""}
                      onChange={(event) => setQuotas({ ...quotas, five_hour: parseOptionalNumber(event.target.value) })}
                    />
                  </div>
                  <div className="grid gap-1">
                    <Label>每周</Label>
                    <Input
                      type="number"
                      min={0}
                      value={quotas.weekly ?? ""}
                      onChange={(event) => setQuotas({ ...quotas, weekly: parseOptionalNumber(event.target.value) })}
                    />
                  </div>
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={mutation.isPending}>
            取消
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit}>
            {mutation.isPending && <Loader2 className="animate-spin" />}
            {submitLabel(mode)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
