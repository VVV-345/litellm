// 本文件收集一次性上游 Key、启动解析任务并轮询展示任务状态。
"use client";

import { useMutation, useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Play, TriangleAlert } from "lucide-react";
import { useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
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

import { accountPoolKeys, getParserTask, startParserTask } from "../api";
import type { ChannelSummary, ParserTaskView } from "../types";
import { EphemeralCredentialField } from "./AccountPoolFormFields";

interface ParserTaskDialogProps {
  accessToken: string;
  channel: ChannelSummary;
  onClose: () => void;
  onCompleted: () => Promise<void>;
}

const TaskStatusIcon = ({ status, loading }: { status: string | undefined; loading: boolean }) => {
  if (status === "running" || loading) return <Loader2 className="size-8 animate-spin text-muted-foreground" />;
  if (status === "completed") return <CheckCircle2 className="size-8 text-emerald-600" />;
  return <TriangleAlert className="size-8 text-destructive" />;
};

export default function ParserTaskDialog({ accessToken, channel, onClose, onCompleted }: ParserTaskDialogProps) {
  const [apiKey, setApiKey] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [group, setGroup] = useState(channel.group ?? "");
  const [taskId, setTaskId] = useState<string | null>(null);

  const taskQueryOptions: UseQueryOptions<ParserTaskView> = {
    queryKey: accountPoolKeys.task(channel.channel_id, taskId ?? "pending"),
    queryFn: () => getParserTask(accessToken, channel.channel_id, taskId!),
    enabled: taskId !== null,
    refetchInterval: (query) => (query.state.data?.task.status === "running" ? 1000 : false),
  };
  const taskQuery = useQuery(taskQueryOptions);

  const startMutation = useMutation({
    mutationFn: () => {
      const request = {
        provider_id: "generic",
        api_key: apiKey,
        group: group.trim() || null,
        explicit_parser_id: null,
        openai_compatible: false,
        username: username.trim() || null,
        password: password || null,
      };
      return startParserTask(accessToken, channel.channel_id, request);
    },
    onSuccess: (accepted) => {
      setApiKey("");
      setUsername("");
      setPassword("");
      setTaskId(accepted.task_id);
      NotificationsManager.success("解析任务已启动");
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const task = taskQuery.data?.task;
  const finished = task && task.status !== "running";
  const successful = task?.status === "completed";

  const close = async () => {
    if (successful) await onCompleted();
    onClose();
  };

  const hasCompleteAdminCredentials = Boolean(username.trim() && password);
  const hasPartialAdminCredentials = Boolean(username.trim()) !== Boolean(password);
  const canStart = !startMutation.isPending && Boolean(apiKey) && !hasPartialAdminCredentials;

  return (
    <Dialog open onOpenChange={(open) => !open && void close()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>解析渠道数据</DialogTitle>
          <DialogDescription>一次性 Key 仅发送到当前 Account Pool 实例，任务接受后立即从表单内存清除</DialogDescription>
        </DialogHeader>

        {taskId === null ? (
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="parser-api-base">上游 URL</Label>
              <Input id="parser-api-base" value={channel.base_url_display} readOnly />
            </div>
            <EphemeralCredentialField id="parser-api-key" label="一次性 Key" value={apiKey} onValueChange={setApiKey} />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="parser-username">管理员账号（可选）</Label>
                <Input
                  id="parser-username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="parser-password">管理员密码（可选）</Label>
                <Input
                  id="parser-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                />
              </div>
            </div>
            {hasPartialAdminCredentials && <p className="text-sm text-destructive">管理员账号与密码必须同时提供</p>}
            <div className="grid gap-2">
              <Label htmlFor="parser-group">分组（可选）</Label>
              <Input id="parser-group" value={group} onChange={(event) => setGroup(event.target.value)} />
            </div>
          </div>
        ) : (
          <div className="flex min-h-44 flex-col items-center justify-center gap-3 text-center">
            <TaskStatusIcon status={task?.status} loading={taskQuery.isLoading} />
            <div>
              <p className="font-medium">{task?.status ?? "正在读取任务状态"}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{taskId}</p>
              {task?.failure_code && <p className="mt-2 text-sm text-destructive">{task.failure_code}</p>}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => void close()} disabled={startMutation.isPending}>
            {finished ? "完成" : "关闭"}
          </Button>
          {taskId === null && (
            <Button onClick={() => startMutation.mutate()} disabled={!canStart}>
              {startMutation.isPending ? <Loader2 className="animate-spin" /> : <Play />}
              启动解析
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
