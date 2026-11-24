/** 展示上号进度、人工接续和分页，密码及授权链接不进入查询缓存。 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import CopyButton from "@/components/shared/CopyButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import { AccountPoolAuthorizationPanel } from "../credentials/AccountPoolAuthorizationPanel";
import {
  actOnboarding,
  authorizeOnboarding,
  revealOnboarding,
  type OnboardingAction,
  type OnboardingAuthorization,
  type OnboardingItem,
  type OnboardingSecrets,
} from "../../api/AccountPoolOnboardingApi";

const mailboxLinks = {
  outlook: "https://account.microsoft.com/security",
  gmail: "https://myaccount.google.com/security",
  mail: null,
};

export function AccountPoolOnboardingTasks({
  accessToken,
  items,
  onChange,
}: {
  accessToken: string;
  items: OnboardingItem[];
  onChange: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [secret, setSecret] = useState<OnboardingSecrets | null>(null);
  const [authorization, setAuthorization] = useState<OnboardingAuthorization | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const selected = items.find((item) => item.id === selectedId);
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(page, pages);
  const visible = [...items].reverse().slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const close = () => {
    setSelectedId(null);
    setSecret(null);
    setAuthorization(null);
    setPassword("");
  };
  useEffect(() => {
    if (!secret) return;
    const timer = setTimeout(() => setSecret(null), 60000);
    return () => clearTimeout(timer);
  }, [secret]);
  const action = async (item: OnboardingItem, body: OnboardingAction) => {
    setBusy(true);
    try {
      await actOnboarding(accessToken, item.id, body);
      setSecret(null);
      setPassword("");
      if (body.action === "generate_password") setSecret(await revealOnboarding(accessToken, item.id));
      toast.success(
        body.action === "generate_password"
          ? t("accountPool.onboarding.tasks.passwordGeneratedToast")
          : t("accountPool.onboarding.tasks.taskUpdatedToast"),
      );
      await onChange();
    } catch {
      toast.error(t("accountPool.onboarding.tasks.actionFailedToast"));
    } finally {
      setBusy(false);
    }
  };
  const reveal = async () => {
    if (!selected) return;
    if (secret) {
      setSecret(null);
      return;
    }
    setBusy(true);
    try {
      setSecret(await revealOnboarding(accessToken, selected.id));
    } catch {
      toast.error(t("accountPool.onboarding.tasks.revealFailedToast"));
    } finally {
      setBusy(false);
    }
  };
  const showAuthorization = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      setAuthorization(await authorizeOnboarding(accessToken, selected.id));
    } catch (error) {
      toast.error(
        error instanceof Error && error.message.trim()
          ? error.message
          : t("accountPool.onboarding.tasks.authorizationUnavailableToast"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t("accountPool.onboarding.tasks.empty")}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="p-3">{t("accountPool.onboarding.tasks.colAccountFile")}</th>
                  <th className="p-3">{t("accountPool.onboarding.tasks.colStatus")}</th>
                  <th className="p-3">{t("accountPool.onboarding.tasks.colCardProgress")}</th>
                  <th className="p-3">{t("accountPool.onboarding.tasks.colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id} className="border-t align-top">
                    <td className="p-3">
                      <p className="break-all font-medium">{item.label}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.supplier}
                        {item.mailbox ? ` · ${item.mailbox} · ${t("accountPool.onboarding.tasks.passwordMasked")}` : ""}
                      </p>
                    </td>
                    <td className="p-3">
                      <Badge variant={item.state === "failed" ? "destructive" : "secondary"}>
                        {t(`accountPool.onboarding.states.${item.state}`)}
                      </Badge>
                    </td>
                    <td className="max-w-sm p-3">
                      <p>{item.card_name ?? t("accountPool.onboarding.tasks.waitingCardVerification")}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{item.message}</p>
                      {item.models.length > 0 && (
                        <p className="mt-1 text-xs">
                          {t("accountPool.onboarding.tasks.modelsCount", { count: item.models.length })}
                        </p>
                      )}
                    </td>
                    <td className="p-3">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          close();
                          setSelectedId(item.id);
                        }}
                      >
                        {t("accountPool.onboarding.tasks.viewAction")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-end gap-3 text-sm">
            <span>
              {t("accountPool.onboarding.tasks.paginationSummary", {
                total: items.length,
                page: currentPage,
                pages,
              })}
            </span>
            <select
              aria-label={t("accountPool.onboarding.tasks.pageSizeAria")}
              className="rounded-md border bg-background p-1"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              {[10, 25, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {t("accountPool.onboarding.tasks.pageSizeOption", { size })}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>
              {t("accountPool.onboarding.tasks.prevPage")}
            </Button>
            <Input
              aria-label={t("accountPool.onboarding.tasks.jumpPageAria")}
              type="number"
              min={1}
              max={pages}
              value={currentPage}
              className="w-20"
              onChange={(event) => setPage(Math.max(1, Math.min(pages, Number(event.target.value) || 1)))}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === pages}
              onClick={() => setPage(currentPage + 1)}
            >
              {t("accountPool.onboarding.tasks.nextPage")}
            </Button>
          </div>
        </>
      )}
      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="break-all">{selected?.label}</DialogTitle>
            <DialogDescription>
              {selected
                ? `${t(`accountPool.onboarding.states.${selected.state}`)} · ${selected.message}`
                : t("accountPool.onboarding.tasks.detailsFallback")}
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="space-y-5">
              <div className="rounded-lg bg-muted/40 p-4 text-sm">
                <p>
                  {t("accountPool.onboarding.tasks.cardLabel", {
                    name: selected.card_name ?? t("accountPool.onboarding.tasks.waitingCardCreation"),
                  })}
                </p>
                <p className="mt-1 break-all text-xs text-muted-foreground">
                  {t("accountPool.onboarding.tasks.cardIdLabel", { id: selected.card_id })}
                </p>
                <p className="mt-1">
                  {t("accountPool.onboarding.tasks.attemptsCount", { count: selected.attempts })}
                </p>
                {selected.models.length > 0 && (
                  <p className="mt-2 break-words">
                    {t("accountPool.onboarding.tasks.modelsLabel", { models: selected.models.join("、") })}
                  </p>
                )}
              </div>
              {selected.source === "oauth" && (
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center justify-between">
                    <h4 className="font-medium">{t("accountPool.onboarding.tasks.accountPasswordsTitle")}</h4>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void reveal()}>
                      {secret
                        ? t("accountPool.onboarding.tasks.hidePasswords")
                        : t("accountPool.onboarding.tasks.revealPasswords")}
                    </Button>
                  </div>
                  {(
                    [
                      ["mailbox_password", t("accountPool.onboarding.tasks.fieldMailboxPassword")],
                      ["supplier_password", t("accountPool.onboarding.tasks.fieldSupplierPassword")],
                      ["totp_secret", t("accountPool.onboarding.tasks.fieldTotpSecret")],
                      ["proposed_password", t("accountPool.onboarding.tasks.fieldProposedPassword")],
                    ] as const
                  ).map(([key, label]) => (
                    <div key={key} className="flex items-center gap-2 text-sm">
                      <span className="w-28 shrink-0">{label}</span>
                      <span className="min-w-0 flex-1 break-all font-mono">
                        {secret ? secret[key] || t("accountPool.onboarding.tasks.unsetValue") : "********"}
                      </span>
                      {secret?.[key] && (
                        <CopyButton
                          value={secret[key]!}
                          label={t("accountPool.onboarding.tasks.copyPrefix", { label })}
                        />
                      )}
                    </div>
                  ))}
                  <p className="text-xs text-muted-foreground">{t("accountPool.onboarding.tasks.secretsHint")}</p>
                  {["awaiting_mailbox", "standby", "disabled", "failed"].includes(selected.state) && (
                    <div className="space-y-3 border-t pt-3">
                      <p className="text-sm">{t("accountPool.onboarding.tasks.mailboxChangeHint")}</p>
                      {selected.mailbox && mailboxLinks[selected.mailbox] ? (
                        <a
                          className="text-sm text-primary underline"
                          href={mailboxLinks[selected.mailbox]!}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {t("accountPool.onboarding.tasks.openMailboxSecurity")}
                        </a>
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          {t("accountPool.onboarding.tasks.mailboxSecurityFallback")}
                        </p>
                      )}
                      <Button
                        className="ml-3"
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void action(selected, { action: "generate_password" })}
                      >
                        {t("accountPool.onboarding.tasks.generateRandomPassword")}
                      </Button>
                      <Label htmlFor="confirmed-mailbox-password">
                        {t("accountPool.onboarding.tasks.confirmedMailboxPasswordLabel")}
                      </Label>
                      <Input
                        id="confirmed-mailbox-password"
                        type="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        placeholder={t("accountPool.onboarding.tasks.confirmedMailboxPasswordPlaceholder")}
                      />
                      <Button
                        disabled={busy}
                        onClick={() =>
                          void action(selected, { action: "mailbox_ready", mailbox_password: password || null })
                        }
                      >
                        {t("accountPool.onboarding.tasks.confirmMailboxReady")}
                      </Button>
                    </div>
                  )}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {["standby", "failed", "disabled"].includes(selected.state) && (
                  <Button
                    disabled={busy}
                    onClick={() => void action(selected, { action: selected.state === "standby" ? "start" : "retry" })}
                  >
                    {selected.state === "standby"
                      ? t("accountPool.onboarding.tasks.startAuthorization")
                      : t("accountPool.onboarding.tasks.retryTask")}
                  </Button>
                )}
                {selected.state === "awaiting_authorization" && (
                  <Button disabled={busy} onClick={() => void showAuthorization()}>
                    {t("accountPool.onboarding.tasks.showAuthorizationSteps")}
                  </Button>
                )}
                {["queued", "standby", "awaiting_mailbox", "awaiting_authorization"].includes(selected.state) && (
                  <Button disabled={busy} variant="outline" onClick={() => void action(selected, { action: "pause" })}>
                    {t("accountPool.onboarding.tasks.pauseTask")}
                  </Button>
                )}
              </div>
              {authorization && selected.state === "awaiting_authorization" && (
                <AccountPoolAuthorizationPanel
                  authorization={authorization}
                  idPrefix={`onboarding-${selected.id}`}
                  accessToken={accessToken}
                  environmentId={selected.card_id}
                />
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
