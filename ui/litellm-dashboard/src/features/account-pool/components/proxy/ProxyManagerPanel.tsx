/** 本文件渲染号池代理网关面板，展示当前节点、延迟检测结果并支持切换出口。 */

import { Check, ChevronDown, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import type {
  AccountPoolEnvironment,
  AccountPoolISPProxy,
  AccountPoolISPProxyUpdateRequest,
  AccountPoolProxyConfigurationProfile,
  AccountPoolProxyGateway,
  AccountPoolProxyGatewayDelay,
} from "../../utils/AccountPoolTypes";
import { useProxyGateways, useProxyGatewayISPProbe } from "../../hooks/useProxyGateways";
import { ISPProxyCatalogPanel } from "./ISPProxyCatalogPanel";
import { AdsPowerSyncPanel } from "./AdsPowerSyncPanel";

interface ProxyManagerPanelProps {
  accessToken: string | null;
  enabled: boolean;
  environments: AccountPoolEnvironment[];
  environmentsLoading: boolean;
}

export const ProxyManagerPanel = ({
  accessToken,
  enabled,
  environments,
  environmentsLoading,
}: ProxyManagerPanelProps) => {
  const { t } = useTranslation();
  const {
    gateways,
    gatewaysLoading,
    gatewaysError,
    configuration,
    delays,
    delaysLoading,
    delaysError,
    refetchGateways,
    nodes,
    nodesLoading,
    nodesError,
    switchMutation,
    addMutation,
    deleteMutation,
    configurationMutation,
    configurations,
    configurationSaveMutation,
    configurationActivateMutation,
    configurationDeleteMutation,
    isps,
    ispSubscription,
    ispSubscriptionLoading,
    ispSubscriptionError,
    ispsLoading,
    ispsError,
    ispImportMutation,
    ispUpdateMutation,
    ispDeleteMutation,
    ispProbeMutation,
    bindISPMutation,
    bindFrontMutation,
    configureISPSubscriptionMutation,
    refreshISPSubscriptionMutation,
  } = useProxyGateways(accessToken, enabled);
  const [configurationContent, setConfigurationContent] = useState("");
  const [configurationUrl, setConfigurationUrl] = useState("");
  const [configurationName, setConfigurationName] = useState("");
  const [configurationFiles, setConfigurationFiles] = useState<File[]>([]);
  const [configurationUpdated, setConfigurationUpdated] = useState(false);
  const [configurationSaved, setConfigurationSaved] = useState(false);

  if (!enabled) return null;

  const switching =
    switchMutation.isPending ||
    bindISPMutation.isPending ||
    bindFrontMutation.isPending ||
    addMutation.isPending ||
    deleteMutation.isPending;
  const configurationBusy = configurationMutation.isPending;
  const profileBusy =
    configurationSaveMutation.isPending ||
    configurationActivateMutation.isPending ||
    configurationDeleteMutation.isPending;
  const configurationDisabled =
    switching || configurationBusy || (!configurationContent.trim() && !configurationUrl.trim());
  const configurationSaveDisabled =
    switching ||
    profileBusy ||
    (!configurationFiles.length &&
      (!configurationName.trim() || (!configurationContent.trim() && !configurationUrl.trim())));
  const handleConfigurationUrlChange = (event: ChangeEvent<HTMLInputElement>) => {
    setConfigurationUrl(event.target.value);
    if (event.target.value.trim()) {
      setConfigurationContent("");
      setConfigurationFiles([]);
    }
    setConfigurationUpdated(false);
    setConfigurationSaved(false);
  };
  const handleConfigurationContentChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setConfigurationContent(event.target.value);
    if (event.target.value.trim()) {
      setConfigurationUrl("");
      setConfigurationFiles([]);
    }
    setConfigurationUpdated(false);
    setConfigurationSaved(false);
  };

  const saveConfigurations = async () => {
    try {
      const requests = configurationFiles.length
        ? await Promise.all(
            configurationFiles.map(async (file) => ({ name: file.name.slice(0, 120), content: await file.text() })),
          )
        : [
            {
              name: configurationName.trim(),
              content: configurationContent.trim() || undefined,
              url: configurationUrl.trim() || undefined,
            },
          ];
      await Promise.all(requests.map((request) => configurationSaveMutation.mutateAsync(request)));
      setConfigurationSaved(true);
      setConfigurationContent("");
      setConfigurationUrl("");
      setConfigurationName("");
      setConfigurationFiles([]);
    } catch {
      setConfigurationSaved(false);
    }
  };

  const handleConfigurationFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setConfigurationFiles(Array.from(event.target.files ?? []));
    setConfigurationContent("");
    setConfigurationUrl("");
    setConfigurationSaved(false);
    setConfigurationUpdated(false);
  };

  const activateConfiguration = async (profile: AccountPoolProxyConfigurationProfile) => {
    try {
      await configurationActivateMutation.mutateAsync(profile.id);
    } catch {
      return;
    }
  };

  return (
    <div className="rounded-md border border-border p-4" data-testid="proxy-manager-panel">
      {accessToken && <AdsPowerSyncPanel key={accessToken} accessToken={accessToken} />}
      <ISPProxyCatalogPanel
        subscription={ispSubscription}
        subscriptionLoading={ispSubscriptionLoading}
        subscriptionFailed={
          ispSubscriptionError || configureISPSubscriptionMutation.isError || refreshISPSubscriptionMutation.isError
        }
        probing={ispProbeMutation.isPending}
        probeFailed={ispProbeMutation.isError}
        onProbe={(ids) => ispProbeMutation.mutate(ids)}
        onConfigureSubscription={async (url) => {
          await configureISPSubscriptionMutation.mutateAsync(url);
        }}
        onRefreshSubscription={async () => {
          await refreshISPSubscriptionMutation.mutateAsync();
        }}
        profiles={isps}
        loading={ispsLoading}
        failed={ispsError}
        busy={
          switching ||
          profileBusy ||
          configurationBusy ||
          ispImportMutation.isPending ||
          configureISPSubscriptionMutation.isPending ||
          refreshISPSubscriptionMutation.isPending ||
          ispUpdateMutation.isPending ||
          ispDeleteMutation.isPending
        }
        mutationFailed={ispImportMutation.isError || ispUpdateMutation.isError || ispDeleteMutation.isError}
        onImport={async (content) => {
          await ispImportMutation.mutateAsync(content);
        }}
        onUpdate={async (profileId, request: AccountPoolISPProxyUpdateRequest) => {
          await ispUpdateMutation.mutateAsync({ profileId, request });
        }}
        onDelete={(profileId) => ispDeleteMutation.mutate(profileId)}
      />
      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{t("accountPool.proxyGateways.title")}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.proxyGateways.description")}</p>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {configuration?.config_path
              ? t("accountPool.proxyGateways.configurationPath", { path: configuration.config_path })
              : t("accountPool.proxyGateways.configurationPathUnavailable")}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.proxyGateways.delayTarget")}</p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={refetchGateways}
            disabled={gatewaysLoading || delaysLoading || switching}
            aria-label={t("accountPool.refresh")}
            title={t("accountPool.refresh")}
          >
            <RefreshCw className={gatewaysLoading || delaysLoading ? "size-4 animate-spin" : "size-4"} />
          </Button>
        </div>
      </div>
      <div className="mt-4 grid gap-3 border-t border-border/60 pt-4">
        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="proxy-gateway-configuration-name">
            {t("accountPool.proxyGateways.configurationName")}
          </label>
          <Input
            id="proxy-gateway-configuration-name"
            value={configurationName}
            onChange={(event) => {
              setConfigurationName(event.target.value);
              setConfigurationSaved(false);
            }}
            disabled={switching || profileBusy}
          />
        </div>
        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="proxy-gateway-configuration-url">
            {t("accountPool.proxyGateways.subscriptionUrl")}
          </label>
          <Input
            id="proxy-gateway-configuration-url"
            value={configurationUrl}
            onChange={handleConfigurationUrlChange}
            placeholder="https://example.com/config.yaml"
            disabled={switching || configurationBusy || profileBusy}
          />
        </div>
        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="proxy-gateway-configuration-content">
            {t("accountPool.proxyGateways.configurationContent")}
          </label>
          <Textarea
            id="proxy-gateway-configuration-content"
            value={configurationContent}
            onChange={handleConfigurationContentChange}
            placeholder={t("accountPool.proxyGateways.configurationContentPlaceholder")}
            rows={5}
            disabled={switching || configurationBusy || profileBusy}
          />
        </div>
        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="proxy-gateway-configuration-files">
            {t("accountPool.proxyGateways.configurationFiles")}
          </label>
          <Input
            id="proxy-gateway-configuration-files"
            type="file"
            accept=".yaml,.yml,.conf"
            multiple
            aria-label={t("accountPool.proxyGateways.configurationFiles")}
            onChange={handleConfigurationFiles}
            disabled={switching || configurationBusy || profileBusy}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setConfigurationUpdated(false);
              configurationMutation.mutate(
                {
                  content: configurationContent.trim() || undefined,
                  url: configurationUrl.trim() || undefined,
                  name: configurationName.trim() || undefined,
                },
                { onSuccess: () => setConfigurationUpdated(true) },
              );
            }}
            disabled={configurationDisabled}
          >
            {configurationBusy
              ? t("accountPool.proxyGateways.configurationUpdating")
              : t("accountPool.proxyGateways.updateConfiguration")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void saveConfigurations()}
            disabled={configurationSaveDisabled}
          >
            {profileBusy
              ? t("accountPool.proxyGateways.configurationSaving")
              : t("accountPool.proxyGateways.saveConfiguration")}
          </Button>
          {configurationUpdated && (
            <span className="text-xs text-muted-foreground" role="status">
              {t("accountPool.proxyGateways.configurationUpdated")}
            </span>
          )}
          {configurationSaved && (
            <span className="text-xs text-muted-foreground" role="status">
              {t("accountPool.proxyGateways.configurationSaved")}
            </span>
          )}
          {(configurationSaveMutation.isError ||
            configurationActivateMutation.isError ||
            configurationDeleteMutation.isError) && (
            <span className="text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.configurationProfileFailed")}
            </span>
          )}
          {configurationMutation.isError && (
            <span className="text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.configurationUpdateFailed")}
            </span>
          )}
        </div>
      </div>
      {configurations.length > 0 && (
        <div className="mt-4 border-t border-border/60 pt-3">
          <p className="text-xs font-medium">{t("accountPool.proxyGateways.configurationProfiles")}</p>
          <div className="mt-2 grid gap-1">
            {configurations.map((profile) => (
              <ConfigurationProfileRow
                key={profile.id}
                profile={profile}
                disabled={profileBusy || switching}
                onActivate={() => void activateConfiguration(profile)}
                onDelete={() => {
                  if (
                    window.confirm(t("accountPool.proxyGateways.deleteConfigurationConfirm", { name: profile.name }))
                  ) {
                    configurationDeleteMutation.mutate(profile.id);
                  }
                }}
              />
            ))}
          </div>
        </div>
      )}
      <Collapsible className="mt-4 border-t border-border/60 pt-3">
        <CollapsibleTrigger className="group flex w-full items-center justify-between text-sm font-medium">
          {t("accountPool.proxyGateways.portSettings", { count: gateways.length })}
          <ChevronDown className="size-4 transition-transform group-aria-expanded:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => addMutation.mutate()}
            disabled={gatewaysLoading || switching || !configuration?.config_path}
          >
            <Plus className="size-4" />
            {t("accountPool.proxyGateways.add")}
          </Button>
          {gatewaysError && (
            <div className="mt-3 flex items-center justify-between gap-2" role="alert">
              <p className="text-xs text-destructive">{t("accountPool.proxyGateways.loadFailed")}</p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={refetchGateways}
                disabled={gatewaysLoading || delaysLoading}
              >
                {t("accountPool.retry")}
              </Button>
            </div>
          )}
          {nodesError && !gatewaysError && (
            <p className="mt-3 text-xs text-muted-foreground">{t("accountPool.proxyGateways.nodesUnavailable")}</p>
          )}
          {switchMutation.isError && (
            <p className="mt-3 text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.switchFailed")}
            </p>
          )}
          {(addMutation.isError || deleteMutation.isError) && (
            <p className="mt-3 text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.mutationFailed")}
            </p>
          )}
          {!gatewaysLoading && !gatewaysError && gateways.length === 0 && (
            <p className="mt-3 text-xs text-muted-foreground">{t("accountPool.proxyGateways.empty")}</p>
          )}
          <div className="mt-3 grid gap-2">
            {gateways.map((gateway) => (
              <GatewayRow
                key={gateway.profile_id}
                gateway={gateway}
                accessToken={accessToken}
                nodes={nodes}
                isps={isps}
                cardCount={
                  environmentsLoading
                    ? null
                    : environments.filter((environment) => environment.proxy_profile_id === gateway.profile_id).length
                }
                delay={delays.find(
                  (result) => result.port === gateway.port && result.current_node === gateway.current_node,
                )}
                delaysLoading={delaysLoading}
                delaysError={delaysError}
                disabled={switching || delaysLoading || nodesLoading || (nodes.length === 0 && isps.length === 0)}
                onSelectNode={(nodeName) => switchMutation.mutate({ port: gateway.port, nodeName })}
                onBindISP={(profileId) => bindISPMutation.mutate({ port: gateway.port, profileId })}
                onBindFront={(nodeName) => bindFrontMutation.mutate({ port: gateway.port, nodeName })}
                onDelete={() => deleteMutation.mutate(gateway.port)}
              />
            ))}
          </div>
          {bindISPMutation.isError && (
            <p className="mt-3 text-xs text-destructive" role="alert">
              {t("accountPool.proxyGateways.switchFailed")}
            </p>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
};

interface ConfigurationProfileRowProps {
  profile: AccountPoolProxyConfigurationProfile;
  disabled: boolean;
  onActivate: () => void;
  onDelete: () => void;
}

const ConfigurationProfileRow = ({ profile, disabled, onActivate, onDelete }: ConfigurationProfileRowProps) => {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 items-center justify-between gap-2 border-b border-border/50 py-1.5">
      <div className="min-w-0">
        <p className="truncate text-xs font-medium">{profile.name}</p>
        <p className="text-xs text-muted-foreground">
          {profile.active
            ? t("accountPool.proxyGateways.configurationActive")
            : new Date(profile.created_at).toLocaleString()}
        </p>
      </div>
      <div className="flex shrink-0 gap-1">
        <Button
          type="button"
          variant={profile.active ? "secondary" : "ghost"}
          size="icon"
          disabled={disabled || profile.active}
          onClick={onActivate}
          aria-label={t("accountPool.proxyGateways.activateConfiguration", { name: profile.name })}
          title={t("accountPool.proxyGateways.activateConfiguration", { name: profile.name })}
        >
          <Check className="size-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="text-destructive"
          disabled={disabled || profile.active}
          onClick={onDelete}
          aria-label={t("accountPool.proxyGateways.deleteConfiguration", { name: profile.name })}
          title={t("accountPool.proxyGateways.deleteConfiguration", { name: profile.name })}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </div>
  );
};

interface GatewayRowProps {
  gateway: AccountPoolProxyGateway;
  accessToken: string | null;
  nodes: { name: string; proxy_type: string }[];
  isps: AccountPoolISPProxy[];
  cardCount: number | null;
  delay?: AccountPoolProxyGatewayDelay;
  delaysLoading: boolean;
  delaysError: boolean;
  disabled: boolean;
  onSelectNode: (nodeName: string) => void;
  onBindISP: (profileId: string) => void;
  onBindFront: (nodeName: string) => void;
  onDelete: () => void;
}

const getDelayStatus = (delay: AccountPoolProxyGatewayDelay | undefined, loading: boolean, failed: boolean) => {
  if (loading) return "checking";
  if (failed) return "error";
  return delay?.status ?? "unchecked";
};

const GatewayRow = ({
  gateway,
  accessToken,
  nodes,
  isps,
  cardCount,
  delay,
  delaysLoading,
  delaysError,
  disabled,
  onSelectNode,
  onBindISP,
  onBindFront,
  onDelete,
}: GatewayRowProps) => {
  const { t } = useTranslation();
  const gatewayProbeQuery = useProxyGatewayISPProbe(accessToken, gateway.port);
  const delayStatus = getDelayStatus(delay, delaysLoading, delaysError);
  const delayText =
    delayStatus === "ok" && delay?.delay_ms != null
      ? t("accountPool.proxyGateways.delayValue", { delay: delay.delay_ms })
      : t(`accountPool.proxyGateways.delay_${delayStatus}`);
  const gatewayDelayStatus = delay?.gateway_status ?? "unchecked";
  const gatewayDelayText =
    gatewayDelayStatus === "ok" && delay?.gateway_delay_ms != null
      ? t("accountPool.proxyGateways.delayValue", { delay: delay.gateway_delay_ms })
      : t(`accountPool.proxyGateways.delay_${gatewayDelayStatus}`);
  const boundISP = isps.find((profile) => profile.id === gateway.isp_profile_id);
  const nodeOptions = nodes.filter(
    (node) =>
      !node.name.startsWith("account-pool-isp-") &&
      !node.name.startsWith("account-pool-front-") &&
      !["DIRECT", "REJECT"].includes(node.name.toUpperCase()) &&
      !["compatible", "direct", "fallback", "loadbalance", "pass", "reject", "relay", "selector", "urltest"].includes(
        node.proxy_type.toLowerCase(),
      ),
  );
  const selectedNodeValue = gateway.current_node ? `node:${gateway.current_node}` : undefined;
  const selectedValue = gateway.isp_profile_id ? `isp:${gateway.isp_profile_id}` : selectedNodeValue;
  const chainBound = gateway.isp_profile_id != null;
  const frontAvailable =
    gateway.front_node_name != null && nodeOptions.some((node) => node.name === gateway.front_node_name);
  const chainReady = chainBound && boundISP?.enabled === true && frontAvailable;
  const currentNodeLabel = gateway.current_node
    ? t("accountPool.proxyGateways.currentNode", { node: gateway.current_node })
    : t("accountPool.proxyGateways.currentNodeUnknown");
  const currentNodeText = (() => {
    if (!chainBound) return currentNodeLabel;
    if (boundISP != null && !boundISP.enabled) {
      return t("accountPool.proxyGateways.blockedISP", { name: boundISP.name });
    }
    if (chainReady) {
      return t("accountPool.proxyGateways.currentISP", {
        name: boundISP?.name ?? t("accountPool.proxyGateways.ispUnknown"),
      });
    }
    return t("accountPool.proxyGateways.blockedISPChain", {
      name: boundISP?.name ?? t("accountPool.proxyGateways.ispUnknown"),
    });
  })();
  return (
    <div className="flex min-w-0 flex-col gap-3 border-b border-border/60 py-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{gateway.name}</p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{currentNodeText}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t("accountPool.proxyGateways.cardUsage", {
            count: cardCount === null ? t("accountPool.proxyGateways.cardUsageLoading") : cardCount,
          })}
        </p>
        <p
          className={`mt-0.5 min-h-4 text-xs tabular-nums ${delayStatus === "timeout" || delayStatus === "error" ? "text-destructive" : "text-muted-foreground"}`}
          title={
            delay && !delaysLoading && !delaysError
              ? t("accountPool.proxyGateways.delayCheckedAt", { time: new Date(delay.checked_at).toLocaleString() })
              : undefined
          }
          aria-live="polite"
        >
          {t("accountPool.proxyGateways.delayLabel", { value: delayText })}
        </p>
        <p
          className={`mt-0.5 min-h-4 text-xs tabular-nums ${gatewayDelayStatus === "timeout" || gatewayDelayStatus === "error" ? "text-destructive" : "text-muted-foreground"}`}
          aria-live="polite"
        >
          {t("accountPool.proxyGateways.gatewayDelayLabel", { value: gatewayDelayText })}
        </p>
      </div>
      <div className="flex min-w-0 flex-col gap-1 sm:w-80 sm:shrink-0">
        <div className="flex min-w-0 gap-1">
          <Select
            value={selectedValue}
            onValueChange={(value) => {
              if (!value) return;
              if (value.startsWith("isp:")) onBindISP(value.slice(4));
              else if (value.startsWith("node:")) onSelectNode(value.slice(5));
            }}
            disabled={disabled}
          >
            <SelectTrigger
              className="w-full"
              aria-label={t("accountPool.proxyGateways.selectNode", { name: gateway.name })}
            >
              <SelectValue placeholder={t("accountPool.proxyGateways.selectNodePlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              {nodeOptions.map((node) => (
                <SelectItem key={node.name} value={`node:${node.name}`}>
                  {node.name}
                </SelectItem>
              ))}
              {isps.map((profile) => (
                <SelectItem key={profile.id} value={`isp:${profile.id}`} disabled={!profile.enabled}>
                  {profile.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="shrink-0 text-destructive"
            disabled={disabled}
            onClick={() => {
              if (window.confirm(t("accountPool.proxyGateways.deleteConfirm", { name: gateway.name }))) onDelete();
            }}
            aria-label={t("accountPool.proxyGateways.delete", { name: gateway.name })}
            title={t("accountPool.proxyGateways.delete", { name: gateway.name })}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
        {gateway.isp_profile_id && (
          <div className="flex min-w-0 gap-1">
            <Select
              value={gateway.front_node_name ?? undefined}
              onValueChange={(value) => {
                if (value) onBindFront(value);
              }}
              disabled={disabled}
            >
              <SelectTrigger
                className="w-full"
                aria-label={t("accountPool.proxyGateways.selectFront", { name: gateway.name })}
              >
                <SelectValue placeholder={t("accountPool.proxyGateways.selectFrontPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {nodeOptions.map((node) => (
                  <SelectItem key={node.name} value={node.name}>
                    {node.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              disabled={disabled || gatewayProbeQuery.isFetching}
              onClick={() => void gatewayProbeQuery.refetch()}
              aria-label={t("accountPool.proxyGateways.probeGateway", { name: gateway.name })}
              title={t("accountPool.proxyGateways.probeGateway", { name: gateway.name })}
            >
              <RefreshCw className={gatewayProbeQuery.isFetching ? "size-4 animate-spin" : "size-4"} />
            </Button>
          </div>
        )}
      </div>
      {gateway.isp_profile_id && gatewayProbeQuery.data && (
        <div className="min-w-0 text-xs text-muted-foreground" aria-live="polite">
          <p>
            {gatewayProbeQuery.data.ip_status === "ok"
              ? t("accountPool.proxyGateways.gatewayProbeExit", {
                  isp: gatewayProbeQuery.data.isp ?? t("accountPool.proxyGateways.ispUnknown"),
                  ip: gatewayProbeQuery.data.exit_ip ?? t("accountPool.proxyGateways.ispUnknown"),
                })
              : t(`accountPool.proxyGateways.gatewayProbeIp_${gatewayProbeQuery.data.ip_status}`)}
          </p>
          <p>
            {gatewayProbeQuery.data.openai_status === "response"
              ? t("accountPool.proxyGateways.gatewayProbeOpenAIResponse", {
                  status: gatewayProbeQuery.data.openai_http_status ?? t("accountPool.proxyGateways.ispUnknown"),
                })
              : t(`accountPool.proxyGateways.gatewayProbeOpenAI_${gatewayProbeQuery.data.openai_status}`)}
          </p>
        </div>
      )}
      {gateway.isp_profile_id && gatewayProbeQuery.isError && (
        <p className="text-xs text-destructive" role="alert">
          {t("accountPool.proxyGateways.gatewayProbeFailed")}
        </p>
      )}
    </div>
  );
};
