/** 本文件处理号池环境创建和授权引导，成功后展示 SSH 隧道与 OAuth 链接。 */

import { ExternalLink, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

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
  const { t, i18n } = useTranslation();
  const [name, setName] = useState("");
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(initialAuthorization);
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken || !trimmedName) {
      toast.error(t("accountPool.create.environmentNameRequired"));
      return;
    }
    setSaving(true);
    try {
      const result = await createAccountPoolEnvironment(accessToken, trimmedName);
      setAuthorization(result);
      onCreated();
      toast.success(t("accountPool.create.created"));
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
          <DialogTitle>
            {authorization ? t("accountPool.create.authorizationTitle") : t("accountPool.create.title")}
          </DialogTitle>
          <DialogDescription>
            {authorization ? t("accountPool.create.authorizationDescription") : t("accountPool.create.description")}
          </DialogDescription>
        </DialogHeader>
        {authorization ? (
          <div className="grid gap-5">
            <div className="grid gap-2">
              <Label htmlFor="account-pool-ssh">{t("accountPool.create.sshTunnelCommand")}</Label>
              <div className="flex items-center gap-2">
                <Input id="account-pool-ssh" value={authorization.ssh_command} readOnly className="font-mono text-xs" />
                <CopyButton value={authorization.ssh_command} label={t("accountPool.create.copySshTunnelCommand")} />
              </div>
            </div>
            <div className="rounded-md border border-border bg-muted/30 p-4">
              <p className="text-sm font-medium">{t("accountPool.create.authorizationLink")}</p>
              <p className="mt-1 break-all text-xs text-muted-foreground">{authorization.authorization_url}</p>
              <Button
                type="button"
                className="mt-3"
                size="sm"
                render={<a href={authorization.authorization_url} target="_blank" rel="noreferrer" />}
              >
                <ExternalLink />
                {t("accountPool.create.openAuthorization")}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t("accountPool.create.authorizationExpires", {
                time: formatDateTime(authorization.expires_at, i18n.language),
              })}
            </p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("common.close")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="grid gap-2">
            <Label htmlFor="account-pool-name">{t("accountPool.create.environmentName")}</Label>
            <Input
              id="account-pool-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              placeholder={t("accountPool.create.environmentNamePlaceholder")}
              autoFocus
            />
            <DialogFooter className="mt-4">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                {t("accountPool.cancel")}
              </Button>
              <Button type="button" onClick={() => void handleCreate()} disabled={saving || !name.trim()}>
                <Plus />
                {saving ? t("accountPool.create.creating") : t("accountPool.createEnvironment")}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
