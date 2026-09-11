/** 本文件从 AI 提供商入口创建卡片，并按供应商展示授权引导。 */

import { Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
import { toast } from "@/lib/toast";

import { createAccountPoolEnvironment } from "./AccountPoolApi";
import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";
import { AccountPoolOpenAICompatibleForm } from "./AccountPoolOpenAICompatibleForm";
import type {
  AccountPoolAuthorization,
  AccountPoolEnvironment,
  AccountPoolSupplier,
} from "./AccountPoolTypes";

interface AccountPoolCreateDialogProps {
  accessToken: string | null;
  initialAuthorization?: AccountPoolAuthorization | null;
  initialSupplier?: AccountPoolSupplier;
  environments?: readonly AccountPoolEnvironment[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export const AccountPoolCreateDialog = ({
  accessToken,
  initialAuthorization = null,
  initialSupplier = "openai_codex",
  environments = [],
  open,
  onOpenChange,
  onCreated,
}: AccountPoolCreateDialogProps) => {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const supplier = initialSupplier;
  const [authorization, setAuthorization] = useState<AccountPoolAuthorization | null>(initialAuthorization);
  const [saving, setSaving] = useState(false);
  const completionReported = useRef(false);
  // 重新授权时查询缓存可能仍是上一次的成功状态，必须等到本次授权之后的版本。
  const currentEnvironment = environments.find(
    (environment) =>
      authorization !== null &&
      environment.id === authorization.environment.id &&
      environment.version > authorization.environment.version,
  );
  const authorizationComplete =
    currentEnvironment &&
    ["ready", "cooling_down", "disabled"].includes(currentEnvironment.status) &&
    !currentEnvironment.configuration_pending;

  useEffect(() => {
    if (!open || !authorizationComplete || completionReported.current) return;
    completionReported.current = true;
    toast.success(t("accountPool.create.authorizationCompleted"));
    onOpenChange(false);
  }, [open, authorizationComplete, onOpenChange, t]);

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken || !trimmedName) {
      toast.error(t("accountPool.create.environmentNameRequired"));
      return;
    }
    setSaving(true);
    const createRequest = { name: trimmedName, provider: "openai" as const, channel: "cliproxyapi" as const, supplier };
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
      setAuthorization(null);
    }
    onOpenChange(nextOpen);
  };

  const dialogTitle = authorization
    ? t("accountPool.create.authorizationTitle")
    : `${t("accountPool.providers.create")} · ${t(`accountPool.supplier.${supplier}`)}`;
  const dialogDescription = authorization
    ? t("accountPool.create.authorizationDescription")
    : t("accountPool.create.description");
  const renderDialogBody = () => {
    if (authorization) {
      return (
        <AccountPoolAuthorizationPanel
          authorization={authorization}
          idPrefix="account-pool"
          error={currentEnvironment?.last_error}
        >
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("common.close")}
            </Button>
          </DialogFooter>
        </AccountPoolAuthorizationPanel>
      );
    }
    if (supplier === "openai_compatible") {
      return (
        <AccountPoolOpenAICompatibleForm
          accessToken={accessToken}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      );
    }
    return (
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
        <div className="flex items-center justify-between rounded-md border bg-muted/30 p-3 text-sm">
          <span className="text-muted-foreground">{t("accountPool.supplier.label")}</span>
          <span>{t(`accountPool.supplier.${supplier}`)} · OAuth</span>
        </div>
        <DialogFooter className="mt-2">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            {t("accountPool.cancel")}
          </Button>
          <Button type="button" onClick={() => void handleCreate()} disabled={saving || !name.trim()}>
            <Plus />
            {saving ? t("accountPool.create.creating") : t("accountPool.providers.create")}
          </Button>
        </DialogFooter>
      </div>
    );
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {dialogTitle}
          </DialogTitle>
          <DialogDescription>
            {dialogDescription}
          </DialogDescription>
        </DialogHeader>
        {renderDialogBody()}
      </DialogContent>
    </Dialog>
  );
};
