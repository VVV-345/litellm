import { skipToken, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { ExternalLink, MonitorUp, Square } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import CopyButton from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractProxyErrorMessage } from "@/lib/http/client";

import { formatDateTime } from "../../utils/AccountPoolFormatters";
import {
  cancelAccountPoolOAuthBrowserSession,
  getAccountPoolOAuthBrowserSession,
  getAccountPoolOAuthBrowserUrl,
  startAccountPoolOAuthBrowserSession,
  type AccountPoolOAuthBrowserSession,
} from "../../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

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
  const isBrowserFlow = authorization.flow === "browser_oauth";

  return (
    <div className="grid gap-5" data-testid="account-pool-authorization-panel">
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
      {isBrowserFlow && accessToken && environmentId ? (
        <AccountPoolServerBrowser
          key={`${accessToken}:${environmentId}`}
          accessToken={accessToken}
          environmentId={environmentId}
        />
      ) : null}
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
      {!isBrowserFlow && (
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <p className="text-sm font-medium">{t("accountPool.create.authorizationLink")}</p>
          <p className="mt-1 break-all text-xs text-muted-foreground">{authorization.authorization_url}</p>
          <Button
            type="button"
            nativeButton={false}
            className="mt-3"
            size="sm"
            render={<a href={authorization.authorization_url} target="_blank" rel="noreferrer" />}
          >
            <ExternalLink />
            {t("accountPool.create.openAuthorization")}
          </Button>
        </div>
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

const isBrowserSessionActive = (session: AccountPoolOAuthBrowserSession) =>
  session.status === "starting" || session.status === "active" || session.status === "callback_pending";

function AccountPoolServerBrowser({ accessToken, environmentId }: { accessToken: string; environmentId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [session, setSession] = useState<AccountPoolOAuthBrowserSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const queryKey = accountPoolQueryKeys.oauthBrowserSession(accessToken, session?.id ?? null);
  const sessionQueryOptions: UseQueryOptions<AccountPoolOAuthBrowserSession> = {
    queryKey,
    queryFn: session ? ({ signal }) => getAccountPoolOAuthBrowserSession(accessToken, session.id, signal) : skipToken,
    refetchInterval: (query) => (!query.state.data || isBrowserSessionActive(query.state.data) ? 2000 : false),
    refetchIntervalInBackground: true,
    retry: false,
    gcTime: 0,
  };
  const sessionQuery = useQuery(sessionQueryOptions);
  const current = sessionQuery.data ?? session;
  const active = current !== null && isBrowserSessionActive(current);
  const visibleError = error ?? (sessionQuery.error ? extractProxyErrorMessage(sessionQuery.error) : null);

  const startBrowser = async () => {
    setBusy(true);
    setError(null);
    try {
      setSession(await startAccountPoolOAuthBrowserSession(accessToken, environmentId));
    } catch (startError) {
      setError(extractProxyErrorMessage(startError));
    } finally {
      setBusy(false);
    }
  };

  const stopBrowser = async () => {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      const cancelled = await cancelAccountPoolOAuthBrowserSession(accessToken, current.id);
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, cancelled);
    } catch (stopError) {
      setError(extractProxyErrorMessage(stopError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-3" data-testid="account-pool-server-browser">
      <p className="text-sm text-muted-foreground">{t("accountPool.create.serverBrowserHelp")}</p>
      {visibleError && (
        <p role="alert" className="break-words text-sm text-destructive">
          {visibleError}
        </p>
      )}
      {current && (
        <p role="status" className="text-sm">
          {t(`accountPool.create.browserStatus.${current.status}`)}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {active ? (
          <Button type="button" size="sm" variant="outline" onClick={() => void stopBrowser()} disabled={busy}>
            <Square />
            {t("accountPool.create.stopServerBrowser")}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            onClick={() => void startBrowser()}
            disabled={busy || current?.status === "completed"}
          >
            <MonitorUp />
            {t("accountPool.create.openServerBrowser")}
          </Button>
        )}
      </div>
      {active && current && (
        <iframe
          title={t("accountPool.create.serverBrowserTitle")}
          src={getAccountPoolOAuthBrowserUrl(current.id)}
          referrerPolicy="no-referrer"
          className="h-[min(70vh,640px)] w-full rounded-md border bg-black"
          data-testid="account-pool-server-browser-frame"
        />
      )}
    </div>
  );
}
