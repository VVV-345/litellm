/** 本文件渲染号池代理网关面板：展示 Clash 端口网关的当前节点并支持切换出口。 */

import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import type { AccountPoolProxyGateway } from "./AccountPoolTypes";
import { useProxyGateways } from "./useProxyGateways";

interface ProxyManagerPanelProps {
  accessToken: string | null;
  enabled: boolean;
}

export const ProxyManagerPanel = ({ accessToken, enabled }: ProxyManagerPanelProps) => {
  const { t } = useTranslation();
  const {
    gateways,
    gatewaysLoading,
    gatewaysError,
    configuration,
    refetchGateways,
    nodes,
    nodesLoading,
    nodesError,
    switchMutation,
  } = useProxyGateways(accessToken, enabled);

  if (!enabled) return null;

  const switching = switchMutation.isPending;

  return (
    <div className="rounded-md border border-border p-4" data-testid="proxy-manager-panel">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">{t("accountPool.proxyGateways.title")}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.proxyGateways.description")}</p>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {configuration?.config_path
              ? t("accountPool.proxyGateways.configurationPath", { path: configuration.config_path })
              : t("accountPool.proxyGateways.configurationPathUnavailable")}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={refetchGateways}
          disabled={gatewaysLoading || switching}
          aria-label={t("accountPool.refresh")}
        >
          {t("accountPool.refresh")}
        </Button>
      </div>
      {gatewaysError && (
        <div className="mt-3 flex items-center justify-between gap-2" role="alert">
          <p className="text-xs text-destructive">{t("accountPool.proxyGateways.loadFailed")}</p>
          <Button type="button" variant="ghost" size="sm" onClick={refetchGateways} disabled={gatewaysLoading}>
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
      {!gatewaysLoading && !gatewaysError && gateways.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">{t("accountPool.proxyGateways.empty")}</p>
      )}
      <div className="mt-3 grid gap-2">
        {gateways.map((gateway) => (
          <GatewayRow
            key={gateway.profile_id}
            gateway={gateway}
            nodes={nodes}
            disabled={switching || nodesLoading || nodes.length === 0}
            onSelect={(nodeName) => switchMutation.mutate({ port: gateway.port, nodeName })}
          />
        ))}
      </div>
    </div>
  );
};

interface GatewayRowProps {
  gateway: AccountPoolProxyGateway;
  nodes: { name: string; proxy_type: string }[];
  disabled: boolean;
  onSelect: (nodeName: string) => void;
}

const GatewayRow = ({ gateway, nodes, disabled, onSelect }: GatewayRowProps) => {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-3 border-b border-border/60 py-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{gateway.name}</p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {gateway.current_node
            ? t("accountPool.proxyGateways.currentNode", { node: gateway.current_node })
            : t("accountPool.proxyGateways.currentNodeUnknown")}
        </p>
      </div>
      <div className="min-w-0 sm:w-52 sm:shrink-0">
        <Select
          value={gateway.current_node ?? undefined}
          onValueChange={(value) => {
            if (typeof value === "string" && value.length > 0) onSelect(value);
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
            {nodes.map((node) => (
              <SelectItem key={node.name} value={node.name}>
                {node.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
};
