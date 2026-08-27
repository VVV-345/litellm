// 本文件装配渠道目录、解析任务、字段差异、人工修正和快照管理主界面。
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Database,
  FileJson,
  HeartPulse,
  Import,
  Loader2,
  ListTree,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  ScrollText,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/shared/PageHeader";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { isProxyAdminRole } from "@/utils/roles";
import { useModelCostMap } from "@/app/(dashboard)/hooks/models/useModelCostMap";

import { accountPoolKeys, getChannels, getProviderServices, probeChannelHealth } from "../api";
import {
  AccountPoolModuleHeader,
  AccountPoolModuleNavigation,
  ChannelModuleWorkspace,
  type AccountPoolWorkspaceName,
} from "./AccountPoolWorkspace";
import ChannelList from "./ChannelList";
import ChannelFormDialog from "./ChannelFormDialog";
import ChannelAggregatePanel from "./ChannelAggregatePanel";
import ChannelLifecycleDialog from "./ChannelLifecycleDialog";
import HealthStatusPanel from "./HealthStatusPanel";
import EventLogPanel from "./EventLogPanel";
import OperationStatusPanel from "./OperationStatusPanel";
import OverviewPanel from "./OverviewPanel";
import ParserDataPanel from "./ParserDataPanel";
import ParserTaskDialog from "./ParserTaskDialog";
import RoutingPanel from "./RoutingPanel";
import SnapshotDialog from "./SnapshotDialog";
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
  const providersQuery = useQuery({
    queryKey: accountPoolKeys.providers(),
    queryFn: () => getProviderServices(accessToken!),
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
            <div className="flex min-h-96 items-center justify-center">
              <Loader2 className="animate-spin text-muted-foreground" />
            </div>
          )}
          {channelsQuery.isError && (
            <div className="rounded-md border border-destructive/30 px-4 py-10 text-center text-sm text-destructive">
              无法读取 Account Pool 渠道目录，请检查 PostgreSQL 和 Account Pool 服务配置
            </div>
          )}
          {channelsQuery.data && (
            <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
              <section className="min-w-0 overflow-hidden rounded-md border bg-background">
                <div className="border-b px-4 py-3">
                  <h2 className="text-sm font-semibold">渠道目录</h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">{channels.length} 个持久化渠道</p>
                </div>
                <ChannelList channels={channels} selectedChannelId={selectedChannelId} onSelect={setChosenChannelId} />
              </section>

              <section className="min-w-0 overflow-hidden rounded-md border bg-background">
                {selectedChannel ? (
                  <>
                    <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="truncate text-base font-semibold">{selectedChannel.display_name}</h2>
                          <Badge variant="outline">{selectedChannel.provider}</Badge>
                          <Badge variant={selectedChannel.administrative_state === "enabled" ? "secondary" : "outline"}>
                            {selectedChannel.administrative_state}
                          </Badge>
                        </div>
                        <p className="mt-1 truncate text-xs text-muted-foreground">
                          {selectedChannel.base_url_display}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {selectedChannel.models.map((model) => (
                            <Badge key={model} variant="outline">
                              {model}
                            </Badge>
                          ))}
                          {selectedChannel.models.length === 0 && (
                            <span className="text-xs text-muted-foreground">暂无启用模型绑定</span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          size="icon"
                          title="查看渠道日志"
                          onClick={() => openChannelEvents(selectedChannel.channel_id)}
                        >
                          <ScrollText />
                          <span className="sr-only">查看渠道日志</span>
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          title="检测渠道"
                          onClick={() => healthProbeMutation.mutate()}
                          disabled={healthProbeMutation.isPending || selectedChannel.administrative_state !== "enabled"}
                        >
                          {healthProbeMutation.isPending ? <Loader2 className="animate-spin" /> : <HeartPulse />}
                          <span className="sr-only">检测渠道</span>
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          title="编辑渠道"
                          onClick={() => setFormMode("edit")}
                          disabled={selectedChannel.administrative_state === "pending_delete"}
                        >
                          <Pencil />
                          <span className="sr-only">编辑渠道</span>
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          title="解绑或删除"
                          onClick={() => setLifecycleDialogOpen(true)}
                          disabled={selectedChannel.administrative_state === "pending_delete"}
                        >
                          <Trash2 />
                          <span className="sr-only">解绑或删除</span>
                        </Button>
                        <Button variant="outline" onClick={() => setSnapshotDialogOpen(true)}>
                          <FileJson />
                          快照
                        </Button>
                        <Button onClick={() => setParserDialogOpen(true)}>
                          <Play />
                          运行解析
                        </Button>
                      </div>
                    </div>
                    <Tabs defaultValue="aggregate" className="min-w-0">
                      <div className="border-b px-4">
                        <TabsList variant="line">
                          <TabsTrigger value="aggregate">综合详情</TabsTrigger>
                          <TabsTrigger value="health">健康与冷却</TabsTrigger>
                          <TabsTrigger value="parser">解析数据</TabsTrigger>
                        </TabsList>
                      </div>
                      <TabsContent value="aggregate" className="min-w-0 p-4">
                        <ChannelAggregatePanel
                          accessToken={accessToken!}
                          channelId={selectedChannel.channel_id}
                          onOpenChannelEvents={openChannelEvents}
                        />
                      </TabsContent>
                      <TabsContent value="health" className="min-w-0 p-4">
                        <HealthStatusPanel accessToken={accessToken!} channelId={selectedChannel.channel_id} />
                      </TabsContent>
                      <TabsContent value="parser" className="min-w-0 p-4">
                        <ParserDataPanel accessToken={accessToken!} channelId={selectedChannel.channel_id} />
                      </TabsContent>
                    </Tabs>
                  </>
                ) : (
                  <div className="flex min-h-96 items-center justify-center text-sm text-muted-foreground">
                    请选择渠道
                  </div>
                )}
              </section>
            </div>
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
          providers={providersQuery.data ?? []}
          onClose={() => setParserDialogOpen(false)}
          onCompleted={refreshSelected}
        />
      )}
      {formMode && (
        <ChannelFormDialog
          accessToken={accessToken!}
          mode={formMode}
          channel={formMode === "edit" ? selectedChannel : null}
          providers={providersQuery.data ?? []}
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
