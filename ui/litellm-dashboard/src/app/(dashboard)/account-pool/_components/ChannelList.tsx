// 本文件展示 PostgreSQL 渠道目录，并负责切换当前解析管理渠道。

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

import {
  accountPoolTableColumnDividerClass,
  administrativeStatePresentation,
  channelPriorityPresentation,
} from "../accountPoolPresentation";
import type { ChannelSummary } from "../types";

interface ChannelListProps {
  channels: ChannelSummary[];
  selectedChannelId: string | null;
  onSelect: (channelId: string) => void;
}

export default function ChannelList({ channels, selectedChannelId, onSelect }: ChannelListProps) {
  if (channels.length === 0) {
    return <div className="px-4 py-10 text-center text-sm text-muted-foreground">PostgreSQL 渠道目录中暂无数据</div>;
  }

  return (
    <Table className={accountPoolTableColumnDividerClass}>
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
              <Badge
                variant="outline"
                className={administrativeStatePresentation[channel.administrative_state].className}
              >
                {administrativeStatePresentation[channel.administrative_state].label}
              </Badge>
            </TableCell>
            <TableCell>
              <Badge variant="outline" className={channelPriorityPresentation[channel.priority].className}>
                {channelPriorityPresentation[channel.priority].label}
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
