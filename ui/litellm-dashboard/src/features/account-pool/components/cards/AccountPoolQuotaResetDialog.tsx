/** 单卡额度查询、确认兑换和受控调用验证；这些操作均不刷新 OAuth Token。 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import {
  consumeQuotaReset,
  getQuotaResetStatus,
  prepareQuotaReset,
  verifyQuotaReset,
  type QuotaResetVerification,
} from "../../api/AccountPoolQuotaResetApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

interface Props {
  accessToken: string;
  environment: { id: string; name: string; enabled_models: readonly string[] };
  onClose: () => void;
}

export function AccountPoolQuotaResetDialog({ accessToken, environment, onClose }: Props) {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const key = accountPoolQueryKeys.quotaReset(accessToken, environment.id);
  const status = useQuery({
    queryKey: key,
    queryFn: () => getQuotaResetStatus(accessToken, environment.id),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const operation = status.data?.operation;
  const observation = status.data?.observation;
  const queryFailed = status.isError || (status.isSuccess && !observation);
  const [confirmed, setConfirmed] = useState(false);
  const [recover, setRecover] = useState(false);
  const [model, setModel] = useState(environment.enabled_models[0] ?? "");
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [verification, setVerification] = useState<QuotaResetVerification | null>(null);
  const busy = useRef(false);
  const reconcile = operation?.status === "uncertain";
  const savedVerification = status.data?.last_verification;
  const savedCall = status.data?.last_api_call;
  const displayedCall = !failed && !pending && !verification && savedCall?.model === model
    && savedCall.verification_at === savedVerification?.observed_at ? savedCall : null;
  const displayedVerification = failed || pending ? null : verification ?? (savedVerification?.model === model ? savedVerification.result : null);
  const run = async (action: "reset" | "verify") => {
    if (busy.current || (action === "reset" && !confirmed)) return;
    busy.current = true;
    setPending(true);
    setFailed(false);
    setVerification(null);
    try {
      if (action === "reset") {
        const prepared =
          operation?.status === "prepared" || operation?.status === "uncertain"
            ? operation
            : await prepareQuotaReset(accessToken, environment.id, operation?.request_id);
        // 即使随后 HTTP 响应丢失，也保留服务器给出的原兑换身份。
        cache.setQueryData(key, { observation, operation: prepared });
        const result = await consumeQuotaReset(accessToken, environment.id, prepared.request_id);
        cache.setQueryData(key, { observation: result.after ?? observation, operation: result });
        setConfirmed(false);
        // HTTP 成功不代表兑换成功；不明确结果不能触发恢复或再次兑换。
        setRecover(result.status === "reset" && result.windows_reset > 0);
        if (result.status === "reset" && result.windows_reset > 0 && model) {
          const checked = await verifyQuotaReset(accessToken, environment.id, model, true);
          setVerification(checked);
          await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
        }
      } else {
        const result = await verifyQuotaReset(accessToken, environment.id, model, recover);
        setVerification(result);
        await cache.invalidateQueries({ queryKey: key });
        if (result.restored)
          await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
      }
    } catch {
      setFailed(true);
      if (action === "reset") await cache.invalidateQueries({ queryKey: key });
    } finally {
      busy.current = false;
      setPending(false);
    }
  };
  const canReset =
    reconcile || operation?.status === "prepared" || Boolean(observation?.eligible && observation.available > 0);
  const verificationLabel = () => {
    if (displayedVerification?.restored) return "accountPool.quotaReset.restored";
    if (displayedVerification?.passed) return "accountPool.quotaReset.passed";
    return "accountPool.quotaReset.failed";
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("accountPool.quotaReset.title", { name: environment.name })}</DialogTitle>
          <DialogDescription>{t("accountPool.quotaReset.notice")}</DialogDescription>
        </DialogHeader>
        <section className="grid gap-3 rounded-md border p-3">
          <p>{t("accountPool.quotaReset.available", { count: observation?.available ?? "?" })}</p>
          {queryFailed ? <p role="alert">{t("accountPool.quotaReset.queryFailed")}</p> : (
            <p className="text-sm text-muted-foreground">
              {t(observation?.eligible ? "accountPool.quotaReset.eligible" : "accountPool.quotaReset.ineligible")}
            </p>
          )}
          <Button variant="outline" disabled={pending || status.isFetching} onClick={() => void status.refetch()}>
            {t("accountPool.quotaReset.query")}
          </Button>
          <Label className="flex items-start gap-2">
            <Checkbox
              checked={confirmed}
              disabled={pending}
              onCheckedChange={(value) => setConfirmed(value === true)}
            />
            {t("accountPool.quotaReset.confirm", { name: environment.name })}
          </Label>
          <Button disabled={pending || !confirmed || !canReset} onClick={() => void run("reset")}>
            {t(reconcile ? "accountPool.quotaReset.reconcile" : "accountPool.quotaReset.reset")}
          </Button>
          {operation && (
            <div role="status" className="grid gap-1 text-sm">
              <p>{t(`accountPool.quotaReset.result.${operation.status}`, { count: operation.windows_reset })}</p>
              <p className="break-all text-muted-foreground">
                {t("accountPool.quotaReset.identity")}: {operation.request_id}
              </p>
              {operation.after && <p>{t("accountPool.quotaReset.after", { count: operation.after.available })}</p>}
              {operation.message && <p>{operation.message}</p>}
            </div>
          )}
        </section>
        <section className="grid gap-3 rounded-md border p-3">
          <p className="text-sm text-muted-foreground">{t("accountPool.quotaReset.verifyNotice")}</p>
          <Select
            value={model}
            disabled={pending}
            onValueChange={(value) => {
              if (value) {
                setModel(value);
                setVerification(null);
              }
            }}
          >
            <SelectTrigger aria-label={t("accountPool.modelTest.model")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {environment.enabled_models.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Label className="flex items-start gap-2">
            <Checkbox checked={recover} disabled={pending} onCheckedChange={(value) => setRecover(value === true)} />
            {t("accountPool.quotaReset.recover")}
          </Label>
          <Button disabled={pending || !model} onClick={() => void run("verify")}>
            {t("accountPool.quotaReset.verify")}
          </Button>
          {displayedCall ? (
            <p role="status">
              {t("accountPool.quotaReset.apiVerified")} · {displayedCall.model} · {displayedCall.endpoint}
              {` · ${new Date(displayedCall.observed_at).toLocaleString()} · ${displayedCall.request_id}`}
            </p>
          ) : displayedVerification && (
            <p role="status">
              {t(verificationLabel())} ({displayedVerification.reason})
              {savedVerification && !verification && ` · ${new Date(savedVerification.observed_at).toLocaleString()}`}
            </p>
          )}
        </section>
        {failed && <p role="alert">{t("accountPool.quotaReset.error")}</p>}
        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            {t("accountPool.quotaReset.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
