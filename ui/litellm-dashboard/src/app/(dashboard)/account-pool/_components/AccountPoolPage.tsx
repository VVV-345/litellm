// 本文件装配渠道目录、解析任务、字段差异、人工修正和快照管理主界面。
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Database,
  HeartPulse,
  Import,
  Loader2,
  ListTree,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/shared/PageHeader";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { isProxyAdminRole } from "@/utils/roles";
import { useModelCostMap } from "@/app/(dashboard)/hooks/models/useModelCostMap";

import { accountPoolKeys, getChannels, getUpstreamProviders, probeChannelHealth } from "../api";
import {
  AccountPoolModuleHeader,
  AccountPoolModuleNavigation,
  ChannelModuleWorkspace,
  type AccountPoolWorkspaceName,
} from "./AccountPoolWorkspace";
import ChannelCatalogWorkspace from "./ChannelCatalogWorkspace";
import ChannelFormDialog from "./ChannelFormDialog";
import ChannelLifecycleDialog from "./ChannelLifecycleDialog";
import HealthStatusPanel from "./HealthStatusPanel";
import EventLogPanel from "./EventLogPanel";
import OperationStatusPanel from "./OperationStatusPanel";
import OverviewPanel from "./OverviewPanel";
import ParserDataPanel from "./ParserDataPanel";
import ParserTaskDialog from "./ParserTaskDialog";
import RoutingPanel from "./RoutingPanel";
import SnapshotDialog from "./SnapshotDialog";
import { AccountPoolQueryState } from "./AccountPoolPanel";
import type { ChannelOperation } from "../types";

type ChannelFormMode = "create" | "edit" | "import";

interface AccountPoolPageProps {
  accessToken: string | null;
  userRole: string;
}

export default function AccountPoolPage({ accessToken, userRole }: AccountPoolPageProps) {
  const queryClient = useQueryClient();
  const [chosenChannelId, setChosenChannelId] = useState<string | null>(null);
  const [parserDialogOpen, setParserDialogOpen] = useState(false);
  const [snapshotDialogOpen, setSnapshotDialogOpen] = useState(false);
  const [formMode, setFormMode] = useState<ChannelFormMode | null>(null);
  const [lifecycleDialogOpen, setLifecycleDialogOpen] = useState(false);
  const [operation, setOperation] = useState<ChannelOperation | null>(null);
  const [workspace, setWorkspace] = useState<AccountPoolWorkspaceName>("overview");
  const [eventChannelId, setEventChannelId] = useState<string | null>(null);
  const authorized = isProxyAdminRole(userRole);
  const channelsQuery = useQuery({
    queryKey: accountPoolKeys.channels(),
    queryFn: () => getChannels(accessToken!),
    enabled: Boolean(accessToken && authorized),
  });
  const upstreamProvidersQuery = useQuery({
    queryKey: accountPoolKeys.upstreamProviders(),
    queryFn: () => getUpstreamProviders(accessToken!),
    enabled: Boolean(accessToken && authorized),
    staleTime: 5 * 60 * 1000,
  });
  const modelCostMapQuery = useModelCostMap();
  const channels = channelsQuery.data?.channels ?? [];
  const selectedChannelId = chosenChannelId ?? channels[0]?.channel_id ?? null;
  const selectedChannel = channels.find((channel) => channel.channel_id === selectedChannelId) ?? null;
  const knownModels =
    modelCostMapQuery.data && typeof modelCostMapQuery.data === "object"
      ? Object.keys(modelCostMapQuery.data).sort()
      : [];
  const healthProbeMutation = useMutation({
    mutationFn: () => probeChannelHealth(accessToken!, selectedChannel!.channel_id),
    onSuccess: async (result) => {
      if (result.status === "succeeded") NotificationsManager.success("渠道检测通过");
      else
        NotificationsManager.warning(
          `渠道检测${result.status === "skipped" ? "未执行" : "失败"}：${result.reason_code}`,
        );
      await queryClient.invalidateQueries({ queryKey: accountPoolKeys.all });
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const refreshSelected = async () => {
    await queryClient.invalidateQueries({ queryKey: accountPoolKeys.all });
  };
  const acceptOperation = async (accepted: ChannelOperation) => {
    setOperation(accepted);
    await refreshSelected();
  };
  const openChannelEvents = (channelId: string) => {
    setEventChannelId(channelId);
    setWorkspace("events");
  };
  const changeWorkspace = (value: string) => {
    if (value === "events") setEventChannelId(null);
    setWorkspace(value as AccountPoolWorkspaceName);
  };

  if (!authorized) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-6 text-center">
        <ShieldAlert className="size-8 text-muted-foreground" />
        <h1 className="text-lg font-semibold">仅 Proxy Admin 可以管理 Account Pool</h1>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-5 px-4 py-5 sm:px-6">
      <PageHeader
        title="Account Pool"
        subtitle="维护渠道目录、解析数据和按模型生效的正式调度表"
        icon={<Database className="size-5" />}
        actions={
          <div className="flex flex-wrap gap-2">
            {workspace === "channels" && (
              <>
                <Button variant="outline" onClick={() => setFormMode("import")}>
                  <Import />
                  导入
                </Button>
                <Button onClick={() => setFormMode("create")}>
                  <Plus />
                  创建渠道
                </Button>
              </>
            )}
            {workspace === "parser" && (
              <Button onClick={() => setParserDialogOpen(true)} disabled={!selectedChannel}>
                <Play />
                运行解析
              </Button>
            )}
            {workspace === "health" && (
              <Button
                variant="outline"
                onClick={() => healthProbeMutation.mutate()}
                disabled={
                  !selectedChannel ||
                  healthProbeMutation.isPending ||
                  selectedChannel.administrative_state !== "enabled"
                }
              >
                {healthProbeMutation.isPending ? <Loader2 className="animate-spin" /> : <HeartPulse />}
                立即探测
              </Button>
            )}
            <Button
              variant="outline"
              size="icon"
              title="刷新"
              onClick={() => void refreshSelected()}
              disabled={channelsQuery.isFetching}
            >
              <RefreshCw className={channelsQuery.isFetching ? "animate-spin" : undefined} />
              <span className="sr-only">刷新</span>
            </Button>
          </div>
        }
      />

      <Tabs value={workspace} onValueChange={changeWorkspace} className="min-w-0">
        <AccountPoolModuleNavigation />
        <TabsContent value="overview" className="mt-4 min-w-0">
          <OverviewPanel accessToken={accessToken!} onOpenChannelEvents={openChannelEvents} />
        </TabsContent>
        <TabsContent value="channels" className="mt-4 min-w-0 space-y-4">
          {operation && (
            <OperationStatusPanel
              accessToken={accessToken!}
              initialOperation={operation}
              onClose={() => setOperation(null)}
            />
          )}

          {channelsQuery.isLoading && (
            <AccountPoolQueryState kind="loading" message="正在读取渠道目录" className="min-h-96" />
          )}
          {channelsQuery.isError && (
            <AccountPoolQueryState
              kind="error"
              message="无法读取 Account Pool 渠道目录，请检查 PostgreSQL 和 Account Pool 服务配置"
              className="min-h-96 rounded-md border border-destructive/30"
            />
          )}
          {channelsQuery.data && (
            <ChannelCatalogWorkspace
              accessToken={accessToken!}
              channels={channels}
              selectedChannel={selectedChannel}
              selectedChannelId={selectedChannelId}
              healthProbePending={healthProbeMutation.isPending}
              onSelectChannel={setChosenChannelId}
              onOpenChannelEvents={openChannelEvents}
              onProbe={() => healthProbeMutation.mutate()}
              onEdit={() => setFormMode("edit")}
              onManageLifecycle={() => setLifecycleDialogOpen(true)}
              onOpenSnapshot={() => setSnapshotDialogOpen(true)}
              onRunParser={() => setParserDialogOpen(true)}
            />
          )}
        </TabsContent>
        <TabsContent value="parser" className="mt-4 min-w-0 space-y-4">
          <AccountPoolModuleHeader
            icon={<ListTree className="size-4" />}
            title="解析器"
            subtitle="查看渠道的模型发现、套餐、按量价格和人工修正"
          />
          <ChannelModuleWorkspace
            channels={channels}
            selectedChannelId={selectedChannelId}
            onSelect={setChosenChannelId}
            emptyMessage="添加渠道后即可运行解析器"
          >
            {selectedChannel && <ParserDataPanel accessToken={accessToken!} channelId={selectedChannel.channel_id} />}
          </ChannelModuleWorkspace>
        </TabsContent>
        <TabsContent value="health" className="mt-4 min-w-0 space-y-4">
          <AccountPoolModuleHeader
            icon={<Activity className="size-4" />}
            title="健康与冷却"
            subtitle="查看健康探测、并发占用、额度窗口和当前排除原因"
          />
          <ChannelModuleWorkspace
            channels={channels}
            selectedChannelId={selectedChannelId}
            onSelect={setChosenChannelId}
            emptyMessage="添加渠道后即可查看健康与冷却状态"
          >
            {selectedChannel && <HealthStatusPanel accessToken={accessToken!} channelId={selectedChannel.channel_id} />}
          </ChannelModuleWorkspace>
        </TabsContent>
        <TabsContent value="routing" className="mt-4 min-w-0">
          <RoutingPanel accessToken={accessToken!} />
        </TabsContent>
        <TabsContent value="events" className="mt-4 min-w-0">
          <EventLogPanel
            key={eventChannelId ?? "all-events"}
            accessToken={accessToken!}
            initialChannelId={eventChannelId}
          />
        </TabsContent>
      </Tabs>

      {parserDialogOpen && selectedChannel && (
        <ParserTaskDialog
          accessToken={accessToken!}
          channel={selectedChannel}
          onClose={() => setParserDialogOpen(false)}
          onCompleted={refreshSelected}
        />
      )}
      {formMode && (
        <ChannelFormDialog
          accessToken={accessToken!}
          mode={formMode}
          channel={formMode === "edit" ? selectedChannel : null}
          providers={upstreamProvidersQuery.data ?? []}
          knownModels={knownModels}
          onClose={() => setFormMode(null)}
          onAccepted={acceptOperation}
        />
      )}
      {lifecycleDialogOpen && selectedChannel && (
        <ChannelLifecycleDialog
          accessToken={accessToken!}
          channel={selectedChannel}
          onClose={() => setLifecycleDialogOpen(false)}
          onAccepted={acceptOperation}
        />
      )}
      {snapshotDialogOpen && selectedChannel && (
        <SnapshotDialog
          accessToken={accessToken!}
          channel={selectedChannel}
          onClose={() => setSnapshotDialogOpen(false)}
          onImported={refreshSelected}
        />
      )}
    </div>
  );
}
