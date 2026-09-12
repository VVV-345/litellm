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

import {
  createAccountPoolEnvironment,
  createDirectCredentialAccountPoolEnvironment,
  createVertexAccountPoolEnvironment,
} from "./AccountPoolApi";
import { cancelAccountPoolOAuthSession } from "./AccountPoolManagementApi";
import { AccountPoolAuthorizationPanel } from "./AccountPoolAuthorizationPanel";
import { AccountPoolOpenAICompatibleForm } from "./AccountPoolOpenAICompatibleForm";
import type { AccountPoolAuthorization, AccountPoolEnvironment, AccountPoolSupplier } from "./AccountPoolTypes";

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
  const [apiKey, setApiKey] = useState("");
  const [prefix, setPrefix] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [priority, setPriority] = useState(0);
  const [weight, setWeight] = useState(1);
  const [location, setLocation] = useState("us-central1");
  const [vertexFile, setVertexFile] = useState<File | null>(null);
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

  const handleDirectCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken || !trimmedName || !apiKey.trim()) {
      toast.error(t("accountPool.create.directCredentialRequired"));
      return;
    }
    if (supplier !== "gemini" && supplier !== "gemini_interactions") return;
    setSaving(true);
    try {
      await createDirectCredentialAccountPoolEnvironment(accessToken, {
        name: trimmedName,
        supplier,
        credential: {
          api_key: apiKey.trim(),
          prefix: prefix.trim(),
          priority,
          weight,
          ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
          headers: [],
        },
      });
      onCreated();
      toast.success(t("accountPool.create.directCredentialCreated"));
      onOpenChange(false);
    } catch (error) {
      toast.fromError(error);
    } finally {
      setSaving(false);
    }
  };

  const handleVertexCreate = async () => {
    const trimmedName = name.trim();
    if (!accessToken) {
      toast.error(t("accountPool.create.vertexRequired"));
      return;
    }
    const requiredVertexInputMissing = !trimmedName || !vertexFile || !location.trim();
    if (requiredVertexInputMissing) {
      toast.error(t("accountPool.create.vertexRequired"));
      return;
    }
    setSaving(true);
    try {
      await createVertexAccountPoolEnvironment(accessToken, trimmedName, location.trim(), vertexFile);
      onCreated();
      toast.success(t("accountPool.create.vertexCreated"));
      onOpenChange(false);
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
      setApiKey("");
      setPrefix("");
      setBaseUrl("");
      setPriority(0);
      setWeight(1);
      setLocation("us-central1");
      setVertexFile(null);
      setAuthorization(null);
    }
    onOpenChange(nextOpen);
  };

  const cancelAuthorization = async () => {
    if (!accessToken || !authorization) return;
    try {
      await cancelAccountPoolOAuthSession(accessToken, authorization.environment.id);
      toast.success(t("accountPool.create.authorizationCancelled"));
      onCreated();
      onOpenChange(false);
    } catch (error) {
      toast.fromError(error);
    }
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
            <Button type="button" variant="outline" onClick={() => void cancelAuthorization()}>
              {t("accountPool.create.cancelAuthorization")}
            </Button>
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
    if (supplier === "gemini" || supplier === "gemini_interactions") {
      return (
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="account-pool-name">{t("accountPool.create.environmentName")}</Label>
            <Input
              id="account-pool-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              autoFocus
            />
          </div>
          <div className="grid gap-2 rounded-md border p-4">
            <div className="grid gap-2 border-b pb-3">
              <Label htmlFor="account-pool-direct-key">{t("accountPool.create.apiKey")}</Label>
              <Input
                id="account-pool-direct-key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="grid gap-3 pt-1 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="account-pool-direct-prefix">{t("accountPool.create.modelPrefix")}</Label>
                <Input
                  id="account-pool-direct-prefix"
                  value={prefix}
                  onChange={(event) => setPrefix(event.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="account-pool-direct-base-url">{t("accountPool.create.baseUrl")}</Label>
                <Input
                  id="account-pool-direct-base-url"
                  value={baseUrl}
                  onChange={(event) => setBaseUrl(event.target.value)}
                  placeholder="https://..."
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="account-pool-direct-priority">{t("accountPool.create.priority")}</Label>
                <Input
                  id="account-pool-direct-priority"
                  type="number"
                  value={priority}
                  onChange={(event) => setPriority(Number(event.target.value))}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="account-pool-direct-weight">{t("accountPool.create.weight")}</Label>
                <Input
                  id="account-pool-direct-weight"
                  type="number"
                  min={1}
                  max={1000000}
                  value={weight}
                  onChange={(event) => setWeight(Number(event.target.value))}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              {t("accountPool.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => void handleDirectCreate()}
              disabled={saving || !name.trim() || !apiKey.trim()}
            >
              <Plus />
              {saving ? t("accountPool.create.creating") : t("accountPool.providers.create")}
            </Button>
          </DialogFooter>
        </div>
      );
    }
    if (supplier === "vertex") {
      return (
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="account-pool-name">{t("accountPool.create.environmentName")}</Label>
            <Input
              id="account-pool-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              autoFocus
            />
          </div>
          <div className="grid gap-3 rounded-md border p-4">
            <div className="grid gap-2 border-b pb-3">
              <Label htmlFor="account-pool-vertex-file">{t("accountPool.create.vertexFile")}</Label>
              <Input
                id="account-pool-vertex-file"
                type="file"
                accept="application/json,.json"
                onChange={(event) => setVertexFile(event.target.files?.[0] ?? null)}
              />
              <p className="text-xs text-muted-foreground">{t("accountPool.create.vertexFileHint")}</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="account-pool-vertex-location">{t("accountPool.create.vertexLocation")}</Label>
              <Input
                id="account-pool-vertex-location"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              {t("accountPool.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => void handleVertexCreate()}
              disabled={saving || !name.trim() || !vertexFile}
            >
              <Plus />
              {saving ? t("accountPool.create.creating") : t("accountPool.providers.create")}
            </Button>
          </DialogFooter>
        </div>
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
          <span>
            {t(`accountPool.supplier.${supplier}`)} · {t("accountPool.create.oauth")}
          </span>
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
          <DialogTitle>{dialogTitle}</DialogTitle>
          <DialogDescription>{dialogDescription}</DialogDescription>
        </DialogHeader>
        {renderDialogBody()}
      </DialogContent>
    </Dialog>
  );
};
