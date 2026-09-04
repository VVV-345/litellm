/** 本文件处理号池环境创建和授权引导，按渠道供应商与授权流程展示 SSH 隧道或设备码。 */

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import CopyButton from "@/components/shared/CopyButton";
import { toast } from "@/lib/toast";

import { createAccountPoolEnvironment } from "./AccountPoolApi";
import { formatDateTime } from "./AccountPoolFormatters";
import type {
  AccountPoolAuthorization,
  AccountPoolChannel,
  AccountPoolSupplier,
} from "./AccountPoolTypes";

const CLI_PROXY_SUPPLIERS: readonly AccountPoolSupplier[] = [
  "openai_codex",
  "anthropic_claude",
  "google_antigravity",
  "kimi",
  "xai",
] as const;

const CHANNELS: readonly AccountPoolChannel[] = ["cliproxyapi", "freebuff2api"] as const;

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
  const [channel, setChannel] = useState<AccountPoolChannel>("cliproxyapi");
  const [supplier, setSupplier] = useState<AccountPoolSupplier>("openai_codex");
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(initialAuthorization);
  const [saving, setSaving] = useState(false);

  const handleChannelChange = (nextChannel: AccountPoolChannel) => {
    setChannel(nextChannel);
    if (nextChannel !== "cliproxyapi") return;
    if (!CLI_PROXY_SUPPLIERS.includes(supplier)) {
      setSupplier("openai_codex");
    }
  };

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken || !trimmedName) {
      toast.error(t("accountPool.create.environmentNameRequired"));
      return;
    }
    if (channel === "freebuff2api") {
      toast.error(t("accountPool.channel.freebuff2apiNotImplemented"));
      return;
    }
    setSaving(true);
    const createRequest = { name: trimmedName, provider: "openai" as const, channel, supplier };
    try {
      const result = await createAccountPoolEnvironment(accessToken, createRequest);
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
      setChannel("cliproxyapi");
      setSupplier("openai_codex");
      setAuthorization(null);
    }
    onOpenChange(nextOpen);
  };

  const isBrowserFlow = authorization?.flow === "browser_oauth";

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
          <div className="grid gap-5" data-testid="account-pool-authorization-panel">
            {isBrowserFlow && authorization.ssh_command && (
              <div className="grid gap-2" data-testid="account-pool-browser-oauth">
                <Label htmlFor="account-pool-ssh">{t("accountPool.create.sshTunnelCommand")}</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="account-pool-ssh"
                    value={authorization.ssh_command}
                    readOnly
                    className="font-mono text-xs"
                  />
                  <CopyButton value={authorization.ssh_command} label={t("accountPool.create.copySshTunnelCommand")} />
                </div>
              </div>
            )}
            {authorization.flow === "device_code" && authorization.user_code && (
              <div className="grid gap-2" data-testid="account-pool-device-code">
                <Label htmlFor="account-pool-user-code">{t("accountPool.create.userCode")}</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="account-pool-user-code"
                    value={authorization.user_code}
                    readOnly
                    className="font-mono text-xs"
                  />
                  <CopyButton value={authorization.user_code} label={t("accountPool.create.copyUserCode")} />
                </div>
              </div>
            )}
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
          <div className="grid gap-4">
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
            </div>
            <div className="grid gap-2">
              <Label htmlFor="account-pool-channel">{t("accountPool.channel.label")}</Label>
              <Select
                value={channel}
                onValueChange={(value) => handleChannelChange(value as AccountPoolChannel)}
              >
                <SelectTrigger id="account-pool-channel" data-testid="account-pool-channel-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CHANNELS.map((option) => (
                    <SelectItem key={option} value={option} disabled={option === "freebuff2api"}>
                      {t(`accountPool.channel.${option}`)}
                      {option === "freebuff2api" && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {t("accountPool.channel.notImplemented")}
                        </span>
                      )}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {channel === "cliproxyapi" && (
              <div className="grid gap-2">
                <Label htmlFor="account-pool-supplier">{t("accountPool.supplier.label")}</Label>
                <Select value={supplier} onValueChange={(value) => setSupplier(value as AccountPoolSupplier)}>
                  <SelectTrigger id="account-pool-supplier" data-testid="account-pool-supplier-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CLI_PROXY_SUPPLIERS.map((option) => (
                      <SelectItem key={option} value={option}>
                        {t(`accountPool.supplier.${option}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <DialogFooter className="mt-2">
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
