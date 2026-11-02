/** 明确区分凭据轮换和现有访问令牌验证，保留确认与重复提交保护。 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { refreshCardCredentials, verifyCardAccess } from "../../api/AccountPoolCredentialApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

interface Props {
  accessToken: string;
  mode: "refresh" | "verify";
  environment: { id: string; name: string; version: number; enabled_models: readonly string[] };
  onClose: () => void;
}
export function AccountPoolCredentialDialog({ accessToken, mode, environment, onClose }: Props) {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [model, setModel] = useState(environment.enabled_models[0] ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const label = t(`accountPool.credential.${mode}`);
  const run = async () => {
    if (busy.current || submitted || !confirmed || (mode === "verify" && !model)) return;
    busy.current = true;
    setPending(true);
    setSubmitted(true);
    setMessage(null);
    try {
      if (mode === "refresh") {
        const result = await refreshCardCredentials(accessToken, environment.id, environment.version);
        setMessage(
          t(result.saved ? "accountPool.credential.saved" : "accountPool.credential.notSaved", {
            reason: result.reason,
          }),
        );
      } else {
        const result = await verifyCardAccess(accessToken, environment.id, model);
        if (!result.passed) setMessage(t("accountPool.credential.failed"));
        else setMessage(t(result.restored ? "accountPool.credential.temporary" : "accountPool.credential.notRestored"));
      }
    } catch {
      setMessage(t("accountPool.credential.uncertain"));
    } finally {
      await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
      busy.current = false;
      setPending(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {label} · {environment.name}
          </DialogTitle>
          <DialogDescription>{t(`accountPool.credential.${mode}Notice`)}</DialogDescription>
        </DialogHeader>
        {mode === "verify" && (
          <Select value={model} disabled={pending || submitted} onValueChange={(value) => value && setModel(value)}>
            <SelectTrigger aria-label={t("accountPool.availableModels")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {environment.enabled_models.map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Label className="flex items-start gap-2">
          <Checkbox
            checked={confirmed}
            disabled={pending || submitted}
            onCheckedChange={(value) => setConfirmed(value === true)}
          />
          {t("accountPool.credential.confirm", { name: environment.name })}
        </Label>
        {message && (
          <p role="status" className="break-words text-sm">
            {message}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            {t("accountPool.cancel")}
          </Button>
          <Button
            disabled={pending || submitted || !confirmed || (mode === "verify" && !model)}
            onClick={() => void run()}
          >
            {pending ? t("accountPool.credential.running") : label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
