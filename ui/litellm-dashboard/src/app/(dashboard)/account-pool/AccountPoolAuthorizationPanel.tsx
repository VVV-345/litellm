/** 本文件展示可恢复的账号授权信息，不负责发起授权或轮询环境状态。 */

import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

import CopyButton from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { formatDateTime } from "./AccountPoolFormatters";

export interface AccountPoolAuthorizationDetails {
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
}: {
  authorization: AccountPoolAuthorizationDetails;
  idPrefix: string;
  error?: string | null;
  children?: React.ReactNode;
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
      {isBrowserFlow && authorization.ssh_command && (
        <div className="grid gap-2" data-testid="account-pool-browser-oauth">
          <Label htmlFor={`${idPrefix}-ssh`}>{t("accountPool.create.sshTunnelCommand")}</Label>
          <div className="flex items-center gap-2">
            <Input id={`${idPrefix}-ssh`} value={authorization.ssh_command} readOnly className="font-mono text-xs" />
            <CopyButton value={authorization.ssh_command} label={t("accountPool.create.copySshTunnelCommand")} />
          </div>
        </div>
      )}
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
      <p className="text-xs text-muted-foreground">
        {t("accountPool.create.authorizationExpires", {
          time: formatDateTime(authorization.expires_at, i18n.language),
        })}
      </p>
      {children}
    </div>
  );
}
