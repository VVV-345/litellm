/** 提供本机 Camoufox 配置与授权入口，密钥只存在于当前账号页面内存中。 */
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractProxyErrorMessage } from "@/lib/http/client";
import { authorizeAccountPoolEnvironment, exportAccountPoolBrowserProxy } from "../../api/AccountPoolApi";
import { CamoufoxClient, CamoufoxError, sameProxy } from "../../api/CamoufoxClient";
import type { AccountPoolAuthorization } from "../../utils/AccountPoolTypes";

export function AccountPoolCamoufoxBrowser({
  accessToken,
  environmentId,
  busy,
  setBusy,
  onAuthorization,
}: {
  accessToken: string;
  environmentId: string;
  busy: boolean;
  setBusy: (value: boolean) => void;
  onAuthorization: (authorization: AccountPoolAuthorization | null) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [address, setAddress] = useState("http://127.0.0.1:10109");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [allowConsent, setAllowConsent] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);
  const credentialFile = useRef<HTMLInputElement>(null);
  const loginController = useRef<AbortController | null>(null);
  const active = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      loginController.current?.abort();
    };
  }, []);

  const run = async (open: boolean) => {
    if (busy || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setMessage(null);
    if (open) onAuthorization(null);
    try {
      const client = new CamoufoxClient(address, key);
      if (!open) {
        await client.testConnection();
        if (active.current) setMessage(t("accountPool.camoufox.connected"));
        return;
      }
      const selectedFile = credentialFile.current?.files?.[0];
      if (selectedFile && selectedFile.size > 8192) throw new CamoufoxError("credential");
      const credentials = selectedFile ? await selectedFile.text() : null;
      if (credentialFile.current) credentialFile.current.value = "";
      const consent = allowConsent;
      setAllowConsent(false);
      if (!navigator.locks) throw new CamoufoxError("unsupported");
      // 同源标签页共用本机端口锁，防止重复点击或多张卡片同时导入相同代理。
      const port = new URL(client.baseUrl).port;
      await navigator.locks.request(`account-pool-camoufox:${port}`, { ifAvailable: true }, async (lock) => {
        if (!lock) throw new CamoufoxError("busy");
        if (!active.current) return;
        const isp = await exportAccountPoolBrowserProxy(accessToken, environmentId);
        if (!active.current) return;
        setMessage(t("accountPool.camoufox.preparing"));
        const profile = await client.prepare(isp, environmentId);
        if (!active.current) return;
        const authorization = await authorizeAccountPoolEnvironment(accessToken, environmentId);
        if (!active.current) return;
        const currentIsp = await exportAccountPoolBrowserProxy(accessToken, environmentId);
        if (!sameProxy(isp, currentIsp)) throw new CamoufoxError("changed");
        if (!active.current) return;
        await client.open(profile.ticket, authorization.authorization_url);
        if (!active.current) return;
        onAuthorization(authorization);
        setMessage(t(profile.imported ? "accountPool.camoufox.openedImported" : "accountPool.camoufox.openedMatched"));
        if (credentials !== null) {
          const controller = new AbortController();
          loginController.current = controller;
          setLoggingIn(true);
          setMessage(t("accountPool.camoufox.loggingIn"));
          try {
            const result = await client.login(profile.ticket, credentials, consent, controller.signal);
            if (active.current) setMessage(t(`accountPool.camoufox.loginStatus.${result.status}`));
          } catch (failure) {
            if (!controller.signal.aborted) throw failure;
            if (active.current) setMessage(t("accountPool.camoufox.loginStatus.stopped"));
          } finally {
            loginController.current = null;
            if (active.current) setLoggingIn(false);
          }
        }
      });
    } catch (failure) {
      if (active.current) {
        setMessage(null);
        setError(
          failure instanceof CamoufoxError
            ? t(`accountPool.camoufox.errors.${failure.code}`)
            : extractProxyErrorMessage(failure),
        );
      }
    } finally {
      inFlight.current = false;
      if (open && credentialFile.current) credentialFile.current.value = "";
      if (active.current) setBusy(false);
    }
  };

  return (
    <details className="rounded-md border border-border p-4">
      <summary className="cursor-pointer text-sm font-medium">{t("accountPool.camoufox.title")}</summary>
      <div className="mt-3 grid gap-3">
        <p className="text-xs text-muted-foreground">{t("accountPool.camoufox.help")}</p>
        <div className="grid gap-2">
          <Label htmlFor={`${id}-address`}>{t("accountPool.camoufox.address")}</Label>
          <Input
            id={`${id}-address`}
            value={address}
            disabled={busy}
            onChange={(event) => setAddress(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={`${id}-key`}>{t("accountPool.camoufox.key")}</Label>
          <Input
            id={`${id}-key`}
            type="password"
            autoComplete="off"
            value={key}
            disabled={busy}
            onChange={(event) => setKey(event.target.value)}
          />
        </div>
        <p className="text-xs text-muted-foreground">{t("accountPool.camoufox.privacy")}</p>
        <div className="grid gap-2">
          <Label htmlFor={`${id}-credential`}>{t("accountPool.camoufox.credentialFile")}</Label>
          <Input ref={credentialFile} id={`${id}-credential`} type="file" accept=".txt,text/plain" disabled={busy} />
          <p className="text-xs text-muted-foreground">{t("accountPool.camoufox.credentialHelp")}</p>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={allowConsent} disabled={busy} onChange={(event) => setAllowConsent(event.target.checked)} />
            {t("accountPool.camoufox.allowConsent")}
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || !key.trim()}
            onClick={() => void run(false)}
          >
            {t("accountPool.camoufox.test")}
          </Button>
          <Button type="button" size="sm" disabled={busy || !key.trim()} onClick={() => void run(true)}>
            {t("accountPool.camoufox.open")}
          </Button>
          {loggingIn && <Button type="button" size="sm" variant="outline" onClick={() => loginController.current?.abort()}>{t("accountPool.camoufox.stopLogin")}</Button>}
        </div>
        {message && (
          <p role="status" className="text-sm text-muted-foreground">
            {message}
          </p>
        )}
        {error && (
          <p role="alert" className="break-words text-sm text-destructive">
            {error}
          </p>
        )}
        <p className="text-xs text-muted-foreground">{t("accountPool.create.localBrowserCallbackHelp")}</p>
      </div>
    </details>
  );
}
