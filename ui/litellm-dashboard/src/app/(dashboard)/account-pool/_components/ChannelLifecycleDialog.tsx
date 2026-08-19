// 本文件提供渠道解绑、两种删除语义和外部 Deployment 独立删除确认。
"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { accountPoolKeys, deleteChannel, deleteExternalDeployment, detachChannel, getChannel } from "../api";
import type { ChannelOperation, ChannelSummary, DeleteMode } from "../types";

type LifecycleAction = "detach" | DeleteMode | "delete_external";

interface ChannelLifecycleDialogProps {
  accessToken: string;
  channel: ChannelSummary;
  onClose: () => void;
  onAccepted: (operation: ChannelOperation) => Promise<void>;
}

export default function ChannelLifecycleDialog({ accessToken, channel, onClose, onAccepted }: ChannelLifecycleDialogProps) {
  const [action, setAction] = useState<LifecycleAction>("detach");
  const [bindingId, setBindingId] = useState("");
  const detailQuery = useQuery({
    queryKey: accountPoolKeys.channel(channel.channel_id),
    queryFn: () => getChannel(accessToken, channel.channel_id),
  });
  const externalBindings = detailQuery.data?.bindings.filter((binding) => binding.ownership === "externally_managed") ?? [];
  const mutation = useMutation({
    mutationFn: () => {
      if (action === "detach") return detachChannel(accessToken, channel.channel_id);
      if (action === "delete_external") return deleteExternalDeployment(accessToken, channel.channel_id, bindingId);
      return deleteChannel(accessToken, channel.channel_id, action);
    },
    onSuccess: async (operation) => {
      NotificationsManager.success("渠道操作已提交");
      await onAccepted(operation);
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });
  const canSubmit = !mutation.isPending && (action !== "delete_external" || Boolean(bindingId));

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>解绑或删除 {channel.display_name}</DialogTitle>
          <DialogDescription>外部 Deployment 不会被渠道删除隐式移除，删除它必须先单独确认</DialogDescription>
        </DialogHeader>
        <RadioGroup value={action} onValueChange={(value) => setAction(value as LifecycleAction)}>
          <label className="flex gap-3 rounded-md border p-3"><RadioGroupItem value="detach" /><span><span className="block text-sm font-medium">仅解绑</span><span className="text-xs text-muted-foreground">停止调度并保留全部 LiteLLM Deployment</span></span></label>
          <label className="flex gap-3 rounded-md border p-3"><RadioGroupItem value="delete_managed_deployment" /><span><span className="block text-sm font-medium">删除号池管理的 Deployment</span><span className="text-xs text-muted-foreground">删除 pool_managed，保留并解绑 externally_managed</span></span></label>
          <label className="flex gap-3 rounded-md border p-3"><RadioGroupItem value="delete_external" /><span><span className="block text-sm font-medium">单独删除外部 Deployment</span><span className="text-xs text-muted-foreground">这是独立操作，不会同时删除渠道</span></span></label>
        </RadioGroup>
        {action === "delete_external" && (
          <div className="grid gap-2">
            <Label>外部绑定</Label>
            <Select value={bindingId} onValueChange={(value) => setBindingId(value ?? "")}>
              <SelectTrigger className="w-full"><SelectValue placeholder={detailQuery.isLoading ? "读取绑定中" : "选择要删除的外部 Deployment"} /></SelectTrigger>
              <SelectContent>{externalBindings.map((binding) => <SelectItem key={binding.binding_id!} value={binding.binding_id!}>{binding.public_model} · {binding.litellm_deployment_id}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
          <Button variant="destructive" onClick={() => mutation.mutate()} disabled={!canSubmit}>{mutation.isPending && <Loader2 className="animate-spin" />}确认操作</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
