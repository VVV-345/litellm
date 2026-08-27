// 本文件展示 PostgreSQL 渠道目录，并负责切换当前解析管理渠道。

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import type { ChannelPriority, ChannelSummary } from "../types";

interface ChannelListProps {
  channels: ChannelSummary[];
  selectedChannelId: string | null;
  onSelect: (channelId: string) => void;
}

const stateLabel: Record<ChannelSummary["administrative_state"], string> = {
  enabled: "启用",
  paused: "暂停",
  disabled: "停用",
  pending_delete: "待删除",
};

const priorityLabel: Record<ChannelPriority, string> = {
  400: "最高",
  300: "高",
  200: "中",
  100: "低",
};

const stateBadgeClass: Record<ChannelSummary["administrative_state"], string> = {
  enabled: "border-emerald-300 bg-emerald-50 text-emerald-800",
  paused: "border-amber-300 bg-amber-50 text-amber-900",
  disabled: "border-slate-300 bg-slate-100 text-slate-700",
  pending_delete: "border-red-300 bg-red-50 text-red-800",
};

const priorityBadgeClass: Record<ChannelPriority, string> = {
  400: "border-violet-300 bg-violet-50 text-violet-800",
  300: "border-sky-300 bg-sky-50 text-sky-800",
  200: "border-slate-300 bg-slate-50 text-slate-700",
  100: "border-orange-300 bg-orange-50 text-orange-900",
};

export default function ChannelList({ channels, selectedChannelId, onSelect }: ChannelListProps) {
  if (channels.length === 0) {
    return <div className="px-4 py-10 text-center text-sm text-muted-foreground">PostgreSQL 渠道目录中暂无数据</div>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>渠道</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>优先级</TableHead>
          <TableHead className="text-right">绑定</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {channels.map((channel) => (
          <TableRow
            key={channel.channel_id}
            data-state={selectedChannelId === channel.channel_id ? "selected" : undefined}
          >
            <TableCell className="max-w-56 whitespace-normal">
              <Button
                variant="link"
                className="h-auto min-w-0 justify-start p-0 text-left"
                onClick={() => onSelect(channel.channel_id)}
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium">{channel.display_name}</span>
                  <span className="block truncate text-xs font-normal text-muted-foreground">{channel.provider}</span>
                </span>
              </Button>
            </TableCell>
            <TableCell>
              <Badge variant="outline" className={stateBadgeClass[channel.administrative_state]}>
                {stateLabel[channel.administrative_state]}
              </Badge>
            </TableCell>
            <TableCell>
              <Badge variant="outline" className={priorityBadgeClass[channel.priority]}>
                {priorityLabel[channel.priority]}
              </Badge>
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {channel.enabled_binding_count}/{channel.binding_count}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
