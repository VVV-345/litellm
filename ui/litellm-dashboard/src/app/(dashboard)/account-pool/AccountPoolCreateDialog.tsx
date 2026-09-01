/** 本文件处理号池环境创建和授权引导，成功后展示 SSH 隧道与 OAuth 链接。 */

import { ExternalLink, Plus } from "lucide-react";
import { useState } from "react";

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
import CopyButton from "@/components/shared/CopyButton";
import { toast } from "@/lib/toast";

import { createAccountPoolEnvironment } from "./AccountPoolApi";
import { formatDateTime } from "./AccountPoolFormatters";
import type { AccountPoolAuthorization } from "./AccountPoolTypes";

interface AccountPoolCreateDialogProps {
  accessToken: string | null;
  initialAuthorization?: AccountPoolAuthorization | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export const AccountPoolCreateDialog = ({
  accessToken,
  initialAuthorization = null,
  open,
  onOpenChange,
  onCreated,
}: AccountPoolCreateDialogProps) => {
  const [name, setName] = useState("");
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(initialAuthorization);
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken || !trimmedName) {
      toast.error("请输入环境名称");
      return;
    }
    setSaving(true);
    try {
      const result = await createAccountPoolEnvironment(accessToken, trimmedName);
      setAuthorization(result);
      onCreated();
      toast.success("环境已创建，请完成授权");
    } catch (error) {
      toast.fromError(error);
    } finally {
      setSaving(false);
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && saving) return;
    if (!nextOpen) {
      setName("");
      setAuthorization(null);
    }
    onOpenChange(nextOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{authorization ? "完成 OpenAI 授权" : "创建号池环境"}</DialogTitle>
          <DialogDescription>
            {authorization
              ? "先执行 SSH 隧道，再打开授权链接。授权完成后页面会自动刷新环境状态。"
              : "每个环境使用独立的 CLIProxyAPI Compose 网络。"}
          </DialogDescription>
        </DialogHeader>
        {authorization ? (
          <div className="grid gap-5">
            <div className="grid gap-2">
              <Label htmlFor="account-pool-ssh">SSH 隧道命令</Label>
              <div className="flex items-center gap-2">
                <Input id="account-pool-ssh" value={authorization.ssh_command} readOnly className="font-mono text-xs" />
                <CopyButton value={authorization.ssh_command} label="复制 SSH 命令" />
              </div>
            </div>
            <div className="rounded-md border border-border bg-muted/30 p-4">
              <p className="text-sm font-medium">授权链接</p>
              <p className="mt-1 break-all text-xs text-muted-foreground">{authorization.authorization_url}</p>
              <Button
                type="button"
                className="mt-3"
                size="sm"
                render={<a href={authorization.authorization_url} target="_blank" rel="noreferrer" />}
              >
                <ExternalLink />
                打开授权页面
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">授权窗口有效期至 {formatDateTime(authorization.expires_at)}</p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="grid gap-2">
            <Label htmlFor="account-pool-name">环境名称</Label>
            <Input
              id="account-pool-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              placeholder="例如：OpenAI 主账号"
              autoFocus
            />
            <DialogFooter className="mt-4">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                取消
              </Button>
              <Button type="button" onClick={() => void handleCreate()} disabled={saving || !name.trim()}>
                <Plus />
                {saving ? "创建中..." : "创建环境"}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
