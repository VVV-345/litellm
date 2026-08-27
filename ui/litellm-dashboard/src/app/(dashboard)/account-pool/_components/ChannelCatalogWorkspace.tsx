import { FileJson, HeartPulse, Loader2, Pencil, Play, ScrollText, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { ChannelSummary } from "../types";
import { AccountPoolPanel, AccountPoolQueryState } from "./AccountPoolPanel";
import ChannelAggregatePanel from "./ChannelAggregatePanel";
import ChannelList from "./ChannelList";
import HealthStatusPanel from "./HealthStatusPanel";
import ParserDataPanel from "./ParserDataPanel";

interface ChannelCatalogWorkspaceProps {
  accessToken: string;
  channels: ChannelSummary[];
  selectedChannel: ChannelSummary | null;
  selectedChannelId: string | null;
  healthProbePending: boolean;
  onSelectChannel: (channelId: string) => void;
  onOpenChannelEvents: (channelId: string) => void;
  onProbe: () => void;
  onEdit: () => void;
  onManageLifecycle: () => void;
  onOpenSnapshot: () => void;
  onRunParser: () => void;
}

export default function ChannelCatalogWorkspace({
  accessToken,
  channels,
  selectedChannel,
  selectedChannelId,
  healthProbePending,
  onSelectChannel,
  onOpenChannelEvents,
  onProbe,
  onEdit,
  onManageLifecycle,
  onOpenSnapshot,
  onRunParser,
}: ChannelCatalogWorkspaceProps) {
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(280px,360px)_minmax(0,1fr)]">
      <AccountPoolPanel title="渠道目录" description={`${channels.length} 个持久化渠道`}>
        <ChannelList channels={channels} selectedChannelId={selectedChannelId} onSelect={onSelectChannel} />
      </AccountPoolPanel>

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
                <p className="mt-1 truncate text-xs text-muted-foreground">{selectedChannel.base_url_display}</p>
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
                  onClick={() => onOpenChannelEvents(selectedChannel.channel_id)}
                >
                  <ScrollText />
                  <span className="sr-only">查看渠道日志</span>
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  title="检测渠道"
                  onClick={onProbe}
                  disabled={healthProbePending || selectedChannel.administrative_state !== "enabled"}
                >
                  {healthProbePending ? <Loader2 className="animate-spin" /> : <HeartPulse />}
                  <span className="sr-only">检测渠道</span>
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  title="编辑渠道"
                  onClick={onEdit}
                  disabled={selectedChannel.administrative_state === "pending_delete"}
                >
                  <Pencil />
                  <span className="sr-only">编辑渠道</span>
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  title="解绑或删除"
                  onClick={onManageLifecycle}
                  disabled={selectedChannel.administrative_state === "pending_delete"}
                >
                  <Trash2 />
                  <span className="sr-only">解绑或删除</span>
                </Button>
                <Button variant="outline" onClick={onOpenSnapshot}>
                  <FileJson />
                  快照
                </Button>
                <Button onClick={onRunParser}>
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
                  accessToken={accessToken}
                  channelId={selectedChannel.channel_id}
                  onOpenChannelEvents={onOpenChannelEvents}
                />
              </TabsContent>
              <TabsContent value="health" className="min-w-0 p-4">
                <HealthStatusPanel accessToken={accessToken} channelId={selectedChannel.channel_id} />
              </TabsContent>
              <TabsContent value="parser" className="min-w-0 p-4">
                <ParserDataPanel accessToken={accessToken} channelId={selectedChannel.channel_id} />
              </TabsContent>
            </Tabs>
          </>
        ) : (
          <AccountPoolQueryState kind="empty" message="请选择渠道" className="min-h-96" />
        )}
      </section>
    </div>
  );
}
