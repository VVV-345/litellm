// 本文件定义 Account Pool 的模块导航和可复用渠道工作区布局。

import { Activity, LayoutDashboard, ListTree, Route, ScrollText, Settings2 } from "lucide-react";
import type { ReactNode } from "react";

import { TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { ChannelSummary } from "../types";

export type AccountPoolWorkspaceName = "overview" | "channels" | "parser" | "health" | "routing" | "events";

const accountPoolModules = [
  { value: "overview", label: "总览", icon: LayoutDashboard },
  { value: "channels", label: "渠道管理", icon: Settings2 },
  { value: "parser", label: "解析器", icon: ListTree },
  { value: "health", label: "健康与冷却", icon: Activity },
  { value: "routing", label: "模型调度", icon: Route },
  { value: "events", label: "事件日志", icon: ScrollText },
] as const;

export function AccountPoolModuleNavigation() {
  return (
    <TabsList variant="line" className="h-auto w-full flex-wrap justify-start gap-0 border-y bg-background px-1">
      {accountPoolModules.map(({ value, label, icon: Icon }) => (
        <TabsTrigger
          key={value}
          value={value}
          className="gap-2 rounded-none border-r border-border/70 px-3 py-2 last:border-r-0"
        >
          <Icon className="size-4" />
          {label}
        </TabsTrigger>
      ))}
    </TabsList>
  );
}

export function AccountPoolModuleHeader({
  icon,
  title,
  subtitle,
}: {
  icon: ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-start gap-3 border-b pb-3">
      <span className="mt-0.5 flex size-8 flex-none items-center justify-center rounded-md bg-muted text-foreground">
        {icon}
      </span>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
      </div>
    </div>
  );
}

interface ChannelModuleWorkspaceProps {
  channels: ChannelSummary[];
  selectedChannelId: string | null;
  onSelect: (channelId: string) => void;
  emptyMessage: string;
  children: ReactNode;
}

export function ChannelModuleWorkspace({
  channels,
  selectedChannelId,
  onSelect,
  emptyMessage,
  children,
}: ChannelModuleWorkspaceProps) {
  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
      <section className="min-w-0 overflow-hidden rounded-md border bg-background">
        <div className="border-b px-4 py-3">
          <h3 className="text-sm font-semibold">渠道</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{channels.length} 个渠道</p>
        </div>
        <div className="divide-y">
          {channels.map((channel) => (
            <button
              key={channel.channel_id}
              type="button"
              className={`w-full px-4 py-3 text-left transition-colors hover:bg-muted/50 ${
                channel.channel_id === selectedChannelId ? "bg-muted" : ""
              }`}
              onClick={() => onSelect(channel.channel_id)}
            >
              <span className="block truncate text-sm font-medium">{channel.display_name}</span>
              <span className="mt-1 block truncate text-xs text-muted-foreground">
                {channel.provider} · {channel.enabled_binding_count}/{channel.binding_count} 绑定
              </span>
            </button>
          ))}
          {channels.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-muted-foreground">{emptyMessage}</div>
          )}
        </div>
      </section>
      <section className="min-w-0 rounded-md border bg-background p-4">
        {channels.length > 0 ? (
          children
        ) : (
          <div className="flex min-h-72 items-center justify-center text-sm text-muted-foreground">{emptyMessage}</div>
        )}
      </section>
    </div>
  );
}
