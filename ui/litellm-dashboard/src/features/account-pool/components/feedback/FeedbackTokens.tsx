"use client";

import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractProxyErrorMessage } from "@/lib/http/client";
import { createFeedbackToken, listFeedbackTokens, revokeFeedbackToken } from "../../api/AccountPoolFeedbackApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

export function FeedbackTokens({ accessToken, onClose }: { accessToken: string; onClose: () => void }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queryKey = accountPoolQueryKeys.feedbackTokens(accessToken);
  const tokens = useQuery({ queryKey, queryFn: () => listFeedbackTokens(accessToken), retry: false });
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setPlaintext(null);
    try {
      const issued = await createFeedbackToken(accessToken, {
        name: String(data.get("name") ?? "").trim(), expires_in_days: Number(data.get("expires_in_days")),
      });
      setPlaintext(issued.token);
      await client.invalidateQueries({ queryKey });
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const revoke = async (id: string) => {
    setBusy(true);
    setError("");
    setPlaintext(null);
    try {
      await revokeFeedbackToken(accessToken, id);
      await client.invalidateQueries({ queryKey });
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="grid gap-4 rounded-md border p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">{t("feedback.tokens.title")}</h3>
        <Button variant="outline" onClick={onClose}>{t("feedback.tokens.close")}</Button>
      </div>
      <p className="text-sm text-muted-foreground">{t("feedback.tokens.scope")}</p>
      <form onSubmit={create} className="flex flex-wrap items-end gap-3">
        <div className="grid gap-2"><Label htmlFor="feedback-token-name">{t("feedback.tokens.name")}</Label>
          <Input id="feedback-token-name" name="name" required maxLength={80} /></div>
        <div className="grid gap-2"><Label htmlFor="feedback-token-expiry">{t("feedback.tokens.expiry")}</Label>
          <Input id="feedback-token-expiry" name="expires_in_days" type="number" required min={1} max={365} defaultValue={30} /></div>
        <Button type="submit" disabled={busy}>{t("feedback.tokens.create")}</Button>
      </form>
      {plaintext && <div className="grid gap-2" role="status">
        <Label htmlFor="feedback-token-plaintext">{t("feedback.tokens.once")}</Label>
        <Input id="feedback-token-plaintext" readOnly value={plaintext} autoComplete="off" />
        <p className="text-sm">{t("feedback.tokens.warning")}</p>
        <Button variant="outline" onClick={() => setPlaintext(null)}>{t("feedback.tokens.dismiss")}</Button>
      </div>}
      {(error || tokens.error) && <p role="alert" className="text-destructive">{error || extractProxyErrorMessage(tokens.error)}</p>}
      {tokens.isPending && <p role="status">{t("common.loading")}</p>}
      <ul className="grid gap-2">
        {tokens.data?.map((token) => (
          <li key={token.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
            <div><p className="font-medium">{token.name}</p><p>{token.id}</p>
              <p>{t("feedback.tokens.createdAt", { at: token.created_at })}</p>
              <p>{t("feedback.tokens.expiresAt", { at: token.expires_at })}</p>
              {token.revoked_at && <p>{t("feedback.tokens.revokedAt", { at: token.revoked_at })}</p>}</div>
            <Button variant="outline" disabled={busy || Boolean(token.revoked_at)}
              onClick={() => void revoke(token.id)}>{t("feedback.tokens.revoke", { name: token.name })}</Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
