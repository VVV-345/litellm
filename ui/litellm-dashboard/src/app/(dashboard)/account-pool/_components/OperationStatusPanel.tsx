// 本文件轮询展示渠道同步操作状态，并允许失败操作携带可选一次性 Key 重试。
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, X } from "lucide-react";
import { useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { accountPoolKeys, getOperation, reconcileChannel } from "../api";
import type { ChannelOperation } from "../types";
import { EphemeralCredentialInput } from "./AccountPoolFormFields";

interface OperationStatusPanelProps {
  accessToken: string;
  initialOperation: ChannelOperation;
  onClose: () => void;
}

const operationBadgeVariant = (status: ChannelOperation["operation_status"]) => {
  if (status === "failed") return "destructive" as const;
  if (status === "applied") return "secondary" as const;
  return "outline" as const;
};

export default function OperationStatusPanel({ accessToken, initialOperation, onClose }: OperationStatusPanelProps) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const operationId = initialOperation.operation_id;
  const operationQuery = useQuery({
    queryKey: accountPoolKeys.operation(operationId),
    queryFn: () => getOperation(accessToken, operationId),
    initialData: operationId === initialOperation.operation_id ? initialOperation : undefined,
    refetchInterval: (query) => {
      const status = query.state.data?.operation_status;
      return status && !["applied", "failed"].includes(status) ? 1500 : false;
    },
  });
  const operation = operationQuery.data ?? initialOperation;
  const retryMutation = useMutation({
    mutationFn: () => reconcileChannel(accessToken, operation.channel_id, apiKey || null),
    onSuccess: async (nextOperation) => {
      queryClient.setQueryData(accountPoolKeys.operation(nextOperation.operation_id), nextOperation);
      await queryClient.invalidateQueries({ queryKey: accountPoolKeys.channels() });
      NotificationsManager.success("同步重试已提交");
    },
    onError: (error) => NotificationsManager.fromBackend(error),
    onSettled: () => setApiKey(""),
  });
  const terminal = operation.operation_status === "applied" || operation.operation_status === "failed";

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border bg-background px-4 py-3 text-sm">
      {!terminal && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">渠道同步</span>
          <Badge variant={operationBadgeVariant(operation.operation_status)}>{operation.operation_status}</Badge>
        </div>
        <p className="mt-1 truncate font-mono text-xs text-muted-foreground">{operation.operation_id}</p>
        {operation.failure && (
          <p className="mt-1 text-xs text-destructive">
            {operation.failure.code}: {operation.failure.message}
          </p>
        )}
      </div>
      {operation.operation_status === "failed" && (
        <div className="flex min-w-64 flex-1 items-center gap-2">
          <div className="flex-1">
            <EphemeralCredentialInput
              value={apiKey}
              placeholder={operation.requires_key ? "重试需要一次性 Key" : "新 Key（可选）"}
              onValueChange={setApiKey}
            />
          </div>
          <Button
            variant="outline"
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending || (operation.requires_key && !apiKey)}
          >
            {retryMutation.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}重试
          </Button>
        </div>
      )}
      <Button variant="ghost" size="icon" onClick={onClose}>
        <X />
        <span className="sr-only">关闭状态</span>
      </Button>
    </div>
  );
}
