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
  type QuotaResetCredit,
} from "../../api/AccountPoolQuotaResetApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { formatDateTime } from "../../utils/AccountPoolFormatters";

interface Props {
  accessToken: string;
  environment: { id: string; name: string; enabled_models: readonly string[]; provider_family?: string };
  onClose: () => void;
}

type Step = "reset" | "verify";

export function AccountPoolQuotaResetDialog({ accessToken, environment, onClose }: Props) {
  const { t, i18n } = useTranslation();
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
  const [creditId, setCreditId] = useState("");
  const [confirmedChoice, setConfirmedChoice] = useState("");
  const [recover, setRecover] = useState(false);
  const [model, setModel] = useState(environment.enabled_models[0] ?? "");
  const [pending, setPending] = useState<Step | null>(null);
  const inProgress = pending !== null;
  const [failed, setFailed] = useState<Step | null>(null);
  const [verification, setVerification] = useState<QuotaResetVerification | null>(null);
  const busy = useRef(false);
  const reconcile = operation?.status === "uncertain";
  const credits = observation?.cards ?? [];
  const prepared = operation?.status === "prepared" || reconcile;
  const selectedId = prepared ? operation?.credit_id ?? operation?.before.credit_id ?? "" : creditId || observation?.credit_id || "";
  const selected = credits.find((credit) => credit.id === selectedId);
  const choiceIdentity = selected ? JSON.stringify([observation?.provider_scope, selected.id, selected.reset_type, selected.grant_type, selected.expires_at]) : "";
  const confirmationValid = confirmed && confirmedChoice === choiceIdentity;
  const creditLabel = (credit: QuotaResetCredit) => `${t(`accountPool.quotaReset.window.${credit.reset_type}`)} · ${t("accountPool.quotaReset.expires", {
    time: formatDateTime(credit.expires_at, i18n.language),
  })}`;
  const savedVerification = status.data?.last_verification;
  const savedCall = status.data?.last_api_call;
  const displayedCall = !failed && !pending && !verification && savedCall?.model === model
    && savedCall.verification_at === savedVerification?.observed_at ? savedCall : null;
  const displayedVerification = failed || pending ? null : verification ?? (savedVerification?.model === model ? savedVerification.result : null);
  const run = async (action: Step) => {
    if (busy.current || (action === "reset" && !confirmationValid)) return;
    let step = action;
    busy.current = true;
    setPending(step);
    setFailed(null);
    setVerification(null);
    try {
      if (action === "reset") {
        setConfirmed(false);
        setRecover(false);
        const prepared =
          operation?.status === "prepared" || operation?.status === "uncertain"
            ? operation
            : await prepareQuotaReset(accessToken, environment.id, operation?.request_id, selected ? { creditId: selected.id, providerScope: observation?.provider_scope ?? undefined } : undefined);
        // 即使随后 HTTP 响应丢失，也保留服务器给出的原兑换身份。
        cache.setQueryData(key, { observation, operation: prepared });
        if (prepared.status !== "prepared" && prepared.status !== "uncertain") return;
        const result = await consumeQuotaReset(accessToken, environment.id, prepared.request_id);
        cache.setQueryData(key, { observation: result.after ?? observation, operation: result });
        const resetApplied = result.status === "reset" && result.windows_reset > 0;
        if (prepared.status !== "prepared" || !resetApplied || !model) return;
        step = "verify";
        setRecover(true);
        setPending(step);
        const checked = await verifyQuotaReset(accessToken, environment.id, model, true);
        setVerification(checked);
        await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
      } else {
        const result = await verifyQuotaReset(accessToken, environment.id, model, recover);
        setVerification(result);
        await cache.invalidateQueries({ queryKey: key });
        if (result.restored)
          await cache.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
      }
    } catch {
      setFailed(step);
      if (step === "reset") await cache.invalidateQueries({ queryKey: key });
    } finally {
      busy.current = false;
      setPending(null);
    }
  };
  const hasCredit = Boolean(observation?.eligible && observation.available > 0 && (!credits.length || selected?.eligible));
  const canReset = reconcile || (!status.isFetching && !queryFailed && hasCredit);
  const eligibility = observation?.eligible ? "accountPool.quotaReset.eligible" : "accountPool.quotaReset.ineligible";
  const resetAction = reconcile ? "accountPool.quotaReset.reconcile" : "accountPool.quotaReset.reset";
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
          <h3 className="text-sm font-medium">{t("accountPool.quotaReset.redemption")}</h3>
          <p>{t("accountPool.quotaReset.available", { count: observation?.available ?? "?" })}</p>
          {credits.length > 0 && (
            <div className="grid gap-2">
              <Label htmlFor="quota-reset-credit">{t("accountPool.quotaReset.credit")}</Label>
              <Select value={selectedId} disabled={inProgress || prepared} onValueChange={(value) => {
                setCreditId(value ?? "");
                setConfirmed(false);
              }}>
                <SelectTrigger id="quota-reset-credit"><SelectValue>{selected ? creditLabel(selected) : undefined}</SelectValue></SelectTrigger>
                <SelectContent>{credits.map((credit) => (
                  <SelectItem key={credit.id} value={credit.id} disabled={!credit.eligible}>
                    {creditLabel(credit)}
                  </SelectItem>
                ))}</SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">{t("accountPool.quotaReset.glmNotice")}</p>
            </div>
          )}
          {queryFailed ? <p role="alert">{t(environment.provider_family === "glm" ? "accountPool.quotaReset.glmQueryFailed" : "accountPool.quotaReset.queryFailed")}</p> : (
            <p className="text-sm text-muted-foreground">
              {t(status.isPending ? "accountPool.quotaReset.querying" : eligibility)}
            </p>
          )}
          <Button variant="outline" disabled={inProgress || status.isFetching} onClick={() => void status.refetch()}>
            {t("accountPool.quotaReset.query")}
          </Button>
          <Label className="flex items-start gap-2">
            <Checkbox
              checked={confirmationValid}
              disabled={inProgress}
              onCheckedChange={(value) => {
                setConfirmed(value === true);
                setConfirmedChoice(value === true ? choiceIdentity : "");
              }}
            />
            {t(reconcile ? "accountPool.quotaReset.confirmReconcile" : "accountPool.quotaReset.confirm", {
              name: environment.name,
            })}
          </Label>
          <Button disabled={inProgress || !confirmationValid || !canReset} onClick={() => void run("reset")}>
            {t(pending === "reset" ? "accountPool.quotaReset.redeeming" : resetAction)}
          </Button>
          {operation && (
            <div role="status" className="grid gap-1 text-sm">
              <p>{t(operation.status === "reset" && operation.windows_reset === 0
                ? "accountPool.quotaReset.noWindowConfirmed" : `accountPool.quotaReset.result.${operation.status}`,
              { count: operation.windows_reset })}</p>
              <p className="break-all text-muted-foreground">
                {t("accountPool.quotaReset.identity")}: {operation.request_id}
              </p>
              {operation.after && <p>{t("accountPool.quotaReset.after", { count: operation.after.available })}</p>}
              {operation.message && <p>{operation.message}</p>}
            </div>
          )}
          {failed === "reset" && <p role="alert">{t("accountPool.quotaReset.error")}</p>}
        </section>
        <section className="grid gap-3 rounded-md border p-3">
          <h3 className="text-sm font-medium">{t("accountPool.quotaReset.verification")}</h3>
          <p className="text-sm text-muted-foreground">{t("accountPool.quotaReset.verifyNotice")}</p>
          <Select
            value={model}
            disabled={inProgress}
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
            <Checkbox checked={recover} disabled={inProgress} onCheckedChange={(value) => setRecover(value === true)} />
            {t("accountPool.quotaReset.recover")}
          </Label>
          <Button disabled={inProgress || !model} onClick={() => void run("verify")}>
            {t(pending === "verify" ? "accountPool.quotaReset.verifying" : "accountPool.quotaReset.verify")}
          </Button>
          {failed === "verify" && (
            <p role="alert">{t(operation?.status === "reset"
              ? "accountPool.quotaReset.verifyAfterResetError" : "accountPool.quotaReset.verifyError")}</p>
          )}
          {displayedCall ? (
            <p role="status">
              {t("accountPool.quotaReset.apiVerified")} · {displayedCall.model} · {displayedCall.endpoint}
              {` · ${new Date(displayedCall.observed_at).toLocaleString()} · ${displayedCall.request_id}`}
            </p>
          ) : displayedVerification && (
            <p role="status">
              {t(verificationLabel())}
              {!displayedVerification.restored && displayedVerification.reason !== "verified" && (
                <span className="block text-sm text-muted-foreground">
                  {t(`accountPool.quotaReset.reason.${displayedVerification.reason}`, {
                    defaultValue: t("accountPool.quotaReset.checkStatus"),
                  })}
                </span>
              )}
              {savedVerification && !verification && ` · ${new Date(savedVerification.observed_at).toLocaleString()}`}
            </p>
          )}
        </section>
        <DialogFooter>
          <Button variant="outline" disabled={inProgress} onClick={onClose}>
            {t("accountPool.quotaReset.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
