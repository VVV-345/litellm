/** 本文件管理单张卡片凭据，明文仅保留在当前弹窗内存中。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { getProxyBaseUrl } from "@/components/networking";
import { toast } from "@/lib/toast";
import { formatDateTime } from "./AccountPoolFormatters";
import { getCardKeyStatus, issueCardKey, revokeCardKey } from "./AccountPoolManagementApi";

export function AccountPoolKeyDialog({
  accessToken,
  cardId,
  name,
  onClose,
}: {
  accessToken: string;
  cardId: string;
  name: string;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<"rotate" | "revoke" | null>(null);
  const query = useQuery({
    queryKey: ["account-pool", "key-status", cardId, accessToken],
    queryFn: () => getCardKeyStatus(accessToken, cardId),
    retry: false,
  });
  const active = query.data && !query.data.revoked_at;
  const inactiveStatusKey = query.data ? "accountPool.keys.revoked" : "accountPool.keys.none";
  const statusKey = active ? "accountPool.keys.active" : inactiveStatusKey;
  const act = async (action: "create" | "rotate" | "revoke") => {
    setBusy(true);
    setPlaintext(null);
    try {
      if (action === "revoke" && query.data) {
        await revokeCardKey(accessToken, cardId, query.data.key_id);
      } else {
        const result = await issueCardKey(accessToken, cardId, action === "rotate" ? query.data?.key_id : undefined);
        setPlaintext(result.key);
      }
      setConfirmation(null);
      await query.refetch();
    } catch (error) {
      toast.fromError(error);
      await query.refetch();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("accountPool.keys.title", { name })}</DialogTitle>
          <DialogDescription>{t("accountPool.keys.description")}</DialogDescription>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">{t("accountPool.keys.pendingGateway")}</p>
        <Input readOnly aria-label="Base URL" value={`${getProxyBaseUrl().replace(/\/$/, "")}/v1`} />
        {query.isPending && <p role="status">{t("accountPool.management.loading")}</p>}
        {query.isError && (
          <div role="alert">
            <p>{t("accountPool.keys.loadFailed")}</p>
            <Button onClick={() => void query.refetch()}>{t("accountPool.retry")}</Button>
          </div>
        )}
        {!query.isPending && !query.isError && (
          <>
            <p>{t(statusKey)}</p>
            {query.data && (
              <dl className="grid gap-2 text-sm">
                <dt>{t("accountPool.keys.id")}</dt>
                <dd className="break-all">{query.data.key_id}</dd>
                <dt>{t("accountPool.keys.createdAt")}</dt>
                <dd>{formatDateTime(query.data.created_at, i18n.language)}</dd>
                <dt>{t("accountPool.keys.lastUsed")}</dt>
                <dd>{formatDateTime(query.data.last_used_at, i18n.language)}</dd>
              </dl>
            )}
            {plaintext && (
              <div className="grid gap-2">
                <p role="status">{t("accountPool.keys.once")}</p>
                <Input readOnly value={plaintext} aria-label={t("accountPool.keys.plaintext")} />
                <Button
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(plaintext);
                      toast.success(t("accountPool.keys.copied"));
                    } catch {
                      toast.error(t("accountPool.keys.copyFailed"));
                    }
                  }}
                >
                  {t("accountPool.keys.copy")}
                </Button>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {!active && (
                <Button disabled={busy} onClick={() => void act("create")}>
                  {t("accountPool.keys.create")}
                </Button>
              )}
              {active && (
                <>
                  <Button disabled={busy} variant="outline" onClick={() => setConfirmation("rotate")}>
                    {t("accountPool.keys.rotate")}
                  </Button>
                  <Button disabled={busy} variant="destructive" onClick={() => setConfirmation("revoke")}>
                    {t("accountPool.keys.revoke")}
                  </Button>
                </>
              )}
            </div>
            {confirmation && (
              <div className="grid gap-2 rounded-md border p-3">
                <p>{t("accountPool.keys.confirm")}</p>
                <div className="flex gap-2">
                  <Button disabled={busy} onClick={() => void act(confirmation)}>
                    {t("accountPool.management.confirm")}
                  </Button>
                  <Button disabled={busy} variant="outline" onClick={() => setConfirmation(null)}>
                    {t("accountPool.cancel")}
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
