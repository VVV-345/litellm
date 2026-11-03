/** 展示可复制授权链接及手动回填入口，不打开任何浏览器。 */
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import CopyButton from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { submitAccountPoolOAuthCallback } from "../../api/AccountPoolApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { formatDateTime } from "../../utils/AccountPoolFormatters";
import { parseOAuthCallback } from "../../utils/oauthCallback";

interface AccountPoolAuthorizationDetails {
  flow: "browser_oauth" | "device_code";
  authorization_url: string;
  ssh_command: string | null;
  user_code: string | null;
  expires_at: string;
}

export function AccountPoolAuthorizationPanel({
  authorization,
  idPrefix,
  error = null,
  children,
  accessToken = null,
  environmentId = null,
}: {
  authorization: AccountPoolAuthorizationDetails;
  idPrefix: string;
  error?: string | null;
  children?: React.ReactNode;
  accessToken?: string | null;
  environmentId?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const canSubmitCallback = authorization.flow === "browser_oauth" && accessToken && environmentId;
  return (
    <div className="grid gap-5" data-testid="account-pool-authorization-panel">
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="grid gap-2">
        <Label htmlFor={`${idPrefix}-authorization-link`}>{t("accountPool.create.authorizationLink")}</Label>
        <div className="flex items-center gap-2">
          <Input
            id={`${idPrefix}-authorization-link`}
            value={authorization.authorization_url}
            readOnly
            className="text-xs"
          />
          <CopyButton value={authorization.authorization_url} label={t("accountPool.create.copyAuthorizationLink")} />
        </div>
        <p className="text-xs text-muted-foreground">{t("accountPool.create.localBrowserHelp")}</p>
      </div>
      {authorization.flow === "device_code" && authorization.user_code && (
        <div className="grid gap-2" data-testid="account-pool-device-code">
          <Label htmlFor={`${idPrefix}-user-code`}>{t("accountPool.create.userCode")}</Label>
          <div className="flex items-center gap-2">
            <Input
              id={`${idPrefix}-user-code`}
              value={authorization.user_code}
              readOnly
              className="font-mono text-xs"
            />
            <CopyButton value={authorization.user_code} label={t("accountPool.create.copyUserCode")} />
          </div>
        </div>
      )}
      {canSubmitCallback && (
        <ManualOAuthCallback
          key={`${accessToken}:${environmentId}:${authorization.authorization_url}`}
          accessToken={accessToken}
          environmentId={environmentId}
          authorizationUrl={authorization.authorization_url}
          idPrefix={idPrefix}
        />
      )}
      <p className="text-xs text-muted-foreground">
        {t("accountPool.create.authorizationExpires", {
          time: formatDateTime(authorization.expires_at, i18n.language),
        })}
      </p>
      {children}
    </div>
  );
}

function ManualOAuthCallback({
  accessToken,
  environmentId,
  authorizationUrl,
  idPrefix,
}: {
  accessToken: string;
  environmentId: string;
  authorizationUrl: string;
  idPrefix: string;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [callbackUrl, setCallbackUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const inFlight = useRef(false);

  const submit = async () => {
    if (inFlight.current || complete) return;
    setErrorKey(null);
    let callback;
    try {
      callback = parseOAuthCallback(callbackUrl, authorizationUrl);
    } catch {
      setErrorKey("accountPool.create.invalidCallback");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      await submitAccountPoolOAuthCallback(accessToken, environmentId, callback);
      setCallbackUrl("");
      setComplete(true);
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.credentials(accessToken) });
    } catch {
      setErrorKey("accountPool.create.callbackFailed");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-2">
      <Label htmlFor={`${idPrefix}-callback`}>{t("accountPool.create.callbackUrl")}</Label>
      <Input
        id={`${idPrefix}-callback`}
        value={callbackUrl}
        onChange={(event) => setCallbackUrl(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        disabled={busy || complete}
      />
      <p className="text-xs text-muted-foreground">{t("accountPool.create.callbackHelp")}</p>
      <Button type="button" onClick={submit} disabled={busy || complete || !callbackUrl.trim()}>
        {t("accountPool.create.submitCallback")}
      </Button>
      {errorKey && (
        <p role="alert" className="text-sm text-destructive">
          {t(errorKey)}
        </p>
      )}
      {complete && (
        <p role="status" className="text-sm">
          {t("accountPool.create.callbackAccepted")}
        </p>
      )}
    </div>
  );
}
