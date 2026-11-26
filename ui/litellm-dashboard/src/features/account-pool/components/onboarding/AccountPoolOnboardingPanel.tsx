/** 在号池内展示独立的文件导入和 OAuth 上号工作流。 */
"use client";

import Link from "next/link";
import { pageHref } from "@/utils/migratedPages";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FileJson, Mail, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/lib/toast";
import {
  listOnboarding,
  listOnboardingSuppliers,
  parseMailboxAccounts,
  previewOnboarding,
  submitOnboarding,
  type OnboardingImport,
  type OnboardingItem,
  type OnboardingPreview,
} from "../../api/AccountPoolOnboardingApi";
import { createAccountPoolJobId, getAccountPoolSettings } from "../../api/AccountPoolManagementApi";

const ACTIVE_ONBOARDING_STATES = new Set<OnboardingItem["state"]>(["queued", "running", "awaiting_authorization"]);
import { AccountPoolOnboardingTasks } from "./AccountPoolOnboardingTasks";
import { AccountPoolOnboardingCleanup } from "./AccountPoolOnboardingCleanup";
import { mergeAuthFileTemplate } from "../../utils/AuthFileTemplate";
import { AccountPoolOnboardingTarget } from "./AccountPoolOnboardingTarget";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { accountPoolEnvironmentOptions, accountPoolProxyProfileOptions } from "../../hooks/accountPoolOptions";
import { proxyGatewayQueryOptions } from "../../hooks/useProxyGateways";
import type { AccountPoolProxyProfile } from "../../utils/AccountPoolTypes";

import { proxyOptionLabel } from "../../utils/AccountPoolProxyOptions";

interface OnboardingProxySelectorProps {
  accessToken: string;
  optional: boolean;
  profiles: readonly AccountPoolProxyProfile[];
  profilesLoading: boolean;
  profilesError: boolean;
  profilesSuccess: boolean;
  value: string;
  rotation: readonly string[] | null;
  onRotationChange: (ids: string[] | null) => void;
  disabled: boolean;
  onChange: (value: string) => void;
}

const OnboardingProxySelector = ({
  accessToken,
  optional,
  profiles,
  profilesLoading,
  profilesError,
  profilesSuccess,
  value,
  rotation,
  onRotationChange,
  disabled,
  onChange,
}: OnboardingProxySelectorProps) => {
  const { t } = useTranslation();
  const environmentsQuery = useQuery(accountPoolEnvironmentOptions(accessToken));
  const gatewaysQuery = useQuery(proxyGatewayQueryOptions(accessToken));
  const settingsQueryOptions = {
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
  };
  const settingsQuery = useQuery(settingsQueryOptions);
  const defaultProxyProfileId = settingsQuery.data?.values.default_proxy_profile_id ?? undefined;
  const defaultProxyProfileExists = profiles.some((profile) => profile.id === defaultProxyProfileId);
  const defaultProxyNotConfigured = settingsQuery.isSuccess && !defaultProxyProfileId;
  const defaultProxyProfileConfigured = Boolean(defaultProxyProfileId) && profilesSuccess;
  const defaultProxyUnavailable =
    settingsQuery.isSuccess && defaultProxyProfileConfigured && !defaultProxyProfileExists;
  const gateways = gatewaysQuery.data ?? [];
  const profileOptions = [
    ...(optional ? [{ value: "__keep__", label: t("accountPool.onboarding.proxy.keepDefault") }] : []),
    ...profiles.map((profile) => ({
      value: profile.id,
      label: proxyOptionLabel(
        profile,
        gatewaysQuery.isError ? [] : gateways,
        environmentsQuery.isError ? undefined : environmentsQuery.data,
        defaultProxyProfileId,
      ),
    })),
  ];

  const proxyFieldLabel = optional
    ? t("accountPool.onboarding.proxy.authFileProxyLabel")
    : t("accountPool.onboarding.proxy.oauthProxyLabel");

  return (
    <div className="space-y-2">
      <Label htmlFor="onboarding-proxy-profile">{proxyFieldLabel}</Label>
      <Select
        items={profileOptions}
        value={optional && !value ? "__keep__" : value}
        onValueChange={(nextValue) => onChange(nextValue === "__keep__" ? "" : nextValue ?? "")}
        disabled={disabled || rotation !== null || profilesLoading || (!optional && profiles.length === 0)}
      >
        <SelectTrigger
          id="onboarding-proxy-profile"
          aria-label={proxyFieldLabel}
          className="w-full min-w-0"
        >
          <SelectValue placeholder={t("accountPool.onboarding.proxy.selectProxyPlaceholder")} />
        </SelectTrigger>
        <SelectContent align="start" className="min-w-[min(24rem,calc(100vw-2rem))]">
          {profileOptions.map((profile) => (
            <SelectItem key={profile.value} value={profile.value}>
              {profile.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={rotation !== null}
          disabled={disabled || profilesLoading}
          onCheckedChange={(checked) => onRotationChange(checked ? [value].filter(Boolean) : null)}
        />
        {t("accountPool.onboarding.proxy.multiProxyRotation")}
      </label>
      {rotation !== null && (
        <div className="grid gap-2 rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">{t("accountPool.onboarding.proxy.rotationHint")}</p>
          <div className="grid max-h-40 gap-2 overflow-y-auto" aria-label={t("accountPool.onboarding.proxy.rotationAria")}>
            {profileOptions
              .filter((profile) => profile.value !== "__keep__")
              .map((profile) => (
                <label key={profile.value} className="flex min-w-0 items-center gap-2 text-sm">
                  <Checkbox
                    checked={rotation.includes(profile.value)}
                    disabled={disabled}
                    onCheckedChange={(checked) =>
                      onRotationChange(
                        checked ? [...rotation, profile.value] : rotation.filter((id) => id !== profile.value),
                      )
                    }
                  />
                  <span className="min-w-0 break-words">{profile.label}</span>
                  {rotation.includes(profile.value) && (
                    <span aria-hidden="true" className="ml-auto text-xs tabular-nums text-muted-foreground">
                      {rotation.indexOf(profile.value) + 1}
                    </span>
                  )}
                </label>
              ))}
          </div>
          {rotation.length === 0 && (
            <p className="text-xs text-muted-foreground">{t("accountPool.onboarding.proxy.rotationEmpty")}</p>
          )}
        </div>
      )}
      {profilesError && (
        <p role="alert" className="text-sm text-destructive">
          {t("accountPool.onboarding.proxy.profilesLoadError")}
        </p>
      )}
      {profilesSuccess && profiles.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("accountPool.onboarding.proxy.noProfiles")}</p>
      )}
      {defaultProxyNotConfigured && (
        <p className="text-sm text-muted-foreground">{t("accountPool.onboarding.proxy.defaultNotConfigured")}</p>
      )}
      {defaultProxyUnavailable && (
        <p className="text-sm text-destructive" role="alert">
          {t("accountPool.onboarding.proxy.defaultUnavailable")}
        </p>
      )}
      {settingsQuery.isError && (
        <p className="text-sm text-muted-foreground" role="status">
          {t("accountPool.onboarding.proxy.defaultStatusError")}
        </p>
      )}
    </div>
  );
};

export function AccountPoolOnboardingPanel({
  accessToken,
  fileFormat,
  legacyOAuth = false,
}: {
  accessToken: string;
  fileFormat?: "cpa" | "sub2api";
  legacyOAuth?: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [source, setSource] = useState<"auth_file" | "oauth">(legacyOAuth ? "oauth" : "auth_file");
  const [supplier, setSupplier] = useState<OnboardingImport["supplier"]>("openai_codex");
  const [mailbox, setMailbox] = useState<"outlook" | "gmail" | "mail">("outlook");
  const [proxyProfileId, setProxyProfileId] = useState("");
  const [rotationProfiles, setRotationProfiles] = useState<string[] | null>(null);
  const [prepareMailbox, setPrepareMailbox] = useState(true);
  const [useTemplate, setUseTemplate] = useState(false);
  const [request, setRequest] = useState<OnboardingImport | null>(null);
  const [preview, setPreview] = useState<OnboardingPreview[]>([]);
  const [busy, setBusy] = useState(false);
  const suppliersQuery = useQuery({
    queryKey: accountPoolQueryKeys.onboardingSuppliers(accessToken),
    queryFn: () => listOnboardingSuppliers(accessToken),
  });
  const proxyProfilesQuery = useQuery({
    ...accountPoolProxyProfileOptions(accessToken),
    enabled: true,
  });
  const settingsQuery = useQuery({
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
  });
  const template = settingsQuery.data?.values.auth_file_template;
  const singleProxyId =
    proxyProfileId || (source === "auth_file" && useTemplate ? template?.proxy_profile_id : "") || "";
  const effectiveProxyId = rotationProfiles === null ? singleProxyId : rotationProfiles[0] ?? "";
  const proxyProfiles = proxyProfilesQuery.data ?? [];
  const templateProxyLabel = () => {
    if (rotationProfiles !== null || proxyProfileId) return t("accountPool.onboarding.panel.templateProxySelected");
    if (template?.proxy_profile_id === "") return t("accountPool.onboarding.panel.templateProxyCard");
    if (!template?.proxy_profile_id) return t("accountPool.onboarding.panel.templateKeep");
    return (
      proxyProfiles.find((profile) => profile.id === template.proxy_profile_id)?.name ??
      t("accountPool.onboarding.panel.templateProxyUnavailable")
    );
  };
  const rotationInvalid =
    rotationProfiles !== null &&
    (rotationProfiles.length === 0 ||
      rotationProfiles.some((id) => !proxyProfiles.some((profile) => profile.id === id)));
  const proxyProfileSelected =
    rotationProfiles !== null ? !rotationInvalid : proxyProfiles.some((profile) => profile.id === proxyProfileId);
  const supported =
    suppliersQuery.data?.some(
      (item) => item.supplier === supplier && item[source === "oauth" ? "oauth" : "auth_file"],
    ) ?? false;
  const missingOAuthProxy = source === "oauth" && !proxyProfileSelected;
  const missingTemplateProxy =
    source === "auth_file" &&
    useTemplate &&
    Boolean(effectiveProxyId) &&
    !proxyProfiles.some((profile) => profile.id === effectiveProxyId);
  const fileInputDisabled =
    busy ||
    rotationInvalid ||
    !supported ||
    missingOAuthProxy ||
    missingTemplateProxy ||
    (source === "auth_file" && useTemplate && !template);
  const queryKey = accountPoolQueryKeys.onboarding(accessToken);
  const query = useQuery({
    queryKey,
    queryFn: () => listOnboarding(accessToken),
    refetchInterval: (q) =>
      q.state.data?.some((item) => ACTIVE_ONBOARDING_STATES.has(item.state)) ? 5000 : false,
  });
  const reset = () => {
    setRequest(null);
    setPreview([]);
  };
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: accountPoolQueryKeys.onboarding(accessToken) }),
      client.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot }),
    ]);
  };
  const readFiles = async (files: File[]) => {
    reset();
    setBusy(true);
    try {
      if (rotationInvalid) throw new Error(t("accountPool.onboarding.panel.errInvalidRotation"));
      if (!supported) throw new Error(t("accountPool.onboarding.panel.errUnsupportedSupplier"));
      if (source === "oauth" && !proxyProfileSelected) throw new Error(t("accountPool.onboarding.panel.errInvalidOAuthProxy"));
      if (source === "auth_file" && useTemplate && !template) throw new Error(t("accountPool.onboarding.panel.errSaveTemplateFirst"));
      if (missingTemplateProxy) throw new Error(t("accountPool.onboarding.panel.templateProxyMissingError"));
      if (files.length === 0 || files.length > (source === "oauth" ? 1 : 100))
        throw new Error(t("accountPool.onboarding.panel.errBatchLimit"));
      if (
        files.some((file) => file.size > 1024 * 1024) ||
        files.reduce((size, file) => size + file.size, 0) > 8 * 1024 * 1024
      ) {
        throw new Error(t("accountPool.onboarding.panel.errFileSizeLimit"));
      }
      const entries =
        source === "auth_file"
          ? await Promise.all(
              files.map(async (file) => ({
                label: file.name,
                content: await file.text(),
                mailbox_password: "",
                supplier_password: "",
                totp_secret: "",
              })),
            )
          : parseMailboxAccounts(await files[0].text());
      const preparedEntries =
        source === "auth_file" && useTemplate && template
          ? entries.map((entry) => ({ ...entry, content: mergeAuthFileTemplate(entry.content, template) }))
          : entries;
      const body: OnboardingImport = {
        job_id: createAccountPoolJobId(),
        source,
        ...(fileFormat ? { auth_file_format: fileFormat } : {}),
        supplier,
        mailbox,
        prepare_mailbox: prepareMailbox,
        entries: preparedEntries.map((entry, index) =>
          rotationProfiles?.length
            ? { ...entry, proxy_profile_id: rotationProfiles[index % rotationProfiles.length] }
            : entry,
        ),
        ...(effectiveProxyId ? { proxy_profile_id: effectiveProxyId } : {}),
      };
      const checked = await previewOnboarding(accessToken, body);
      setRequest(body);
      setPreview(checked);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("accountPool.onboarding.panel.errFileCheckFailed"));
    } finally {
      setBusy(false);
    }
  };
  const submit = async () => {
    if (!request) return;
    setBusy(true);
    try {
      const result = await submitOnboarding(accessToken, request);
      toast.success(t("accountPool.onboarding.panel.submitSuccessToast", { count: result.items.length }));
      if (result.items.some((item) => item.source !== request.source)) {
        toast.info(t("accountPool.onboarding.panel.submitSplitInfoToast"));
      }
      if (result.items.length > 0 && result.items.every((item) => item.source === "oauth")) setSource("oauth");
      reset();
      await refresh();
    } catch {
      toast.error(t("accountPool.onboarding.panel.submitFailedToast"));
    } finally {
      setBusy(false);
    }
  };

  let importTitle = t("accountPool.onboarding.panel.titleOAuth");
  let importDescription = t("accountPool.onboarding.panel.descOAuth");
  if (fileFormat === "cpa") {
    importTitle = t("accountPool.onboarding.panel.titleCpa");
    importDescription = t("accountPool.onboarding.panel.descCpa");
  } else if (fileFormat === "sub2api") {
    importTitle = t("accountPool.onboarding.panel.titleSub2Api");
    importDescription = t("accountPool.onboarding.panel.descSub2Api");
  } else if (source === "auth_file") {
    importTitle = t("accountPool.onboarding.panel.titleAuthFile");
    importDescription = t("accountPool.onboarding.panel.descAuthFile");
  }
  const fileTasksTitle = fileFormat
    ? t("accountPool.onboarding.panel.fileTasksTitleFormatted")
    : t("accountPool.onboarding.panel.fileTasksTitle");

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        {t("accountPool.onboarding.panel.providerLinkPrefix")}
        <Link href={`${pageHref("account-pool")}?tab=providers`} className="ml-1 underline">
          {t("accountPool.onboarding.panel.providerLinkLabel")}
        </Link>
        {t("accountPool.onboarding.panel.providerLinkSuffix")}
      </p>
      <Tabs
        value={source}
        onValueChange={(value) => {
          if (value === "oauth" || value === "auth_file") {
            setSource(value);
            setProxyProfileId("");
            setRotationProfiles(null);
            setUseTemplate(false);
            reset();
          }
        }}
      >
        {!fileFormat && !legacyOAuth && (
          <TabsList>
            <TabsTrigger value="auth_file">
              <FileJson className="size-4" />
              {t("accountPool.onboarding.panel.tabAuthFile")}
            </TabsTrigger>
            <TabsTrigger value="oauth">
              <Mail className="size-4" />
              {t("accountPool.onboarding.panel.tabOAuth")}
            </TabsTrigger>
          </TabsList>
        )}
        <TabsContent value={source}>
          <Card>
            <CardHeader>
              <CardTitle>{importTitle}</CardTitle>
              <CardDescription>{importDescription}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="onboarding-supplier">{t("accountPool.onboarding.panel.supplierLabel")}</Label>
                  <select
                    id="onboarding-supplier"
                    className="h-9 w-full rounded-md border bg-background px-3"
                    disabled={busy || suppliersQuery.isPending || fileFormat === "sub2api"}
                    value={supplier}
                    onChange={(event) => {
                      setSupplier(event.target.value as OnboardingImport["supplier"]);
                      reset();
                    }}
                  >
                    {(suppliersQuery.data ?? []).map((item) => (
                      <option
                        key={item.supplier}
                        value={item.supplier}
                        disabled={!item[source === "oauth" ? "oauth" : "auth_file"]}
                      >
                        {item.display_name}
                        {item[source === "oauth" ? "oauth" : "auth_file"]
                          ? ""
                          : t("accountPool.onboarding.panel.supplierUnsupportedSuffix", {
                              authentication: item.authentication,
                            })}
                      </option>
                    ))}
                  </select>
                </div>
                <OnboardingProxySelector
                  optional={source === "auth_file"}
                  accessToken={accessToken}
                  profiles={proxyProfiles}
                  profilesLoading={proxyProfilesQuery.isPending}
                  profilesError={proxyProfilesQuery.isError}
                  profilesSuccess={proxyProfilesQuery.isSuccess}
                  value={proxyProfileId}
                  rotation={rotationProfiles}
                  onRotationChange={(ids) => {
                    setRotationProfiles(ids);
                    reset();
                  }}
                  disabled={busy}
                  onChange={(value) => {
                    setProxyProfileId(value);
                    reset();
                  }}
                />
                {source === "oauth" && (
                  <div className="space-y-2">
                    <Label htmlFor="onboarding-mailbox">{t("accountPool.onboarding.panel.mailboxTypeLabel")}</Label>
                    <select
                      id="onboarding-mailbox"
                      className="h-9 w-full rounded-md border bg-background px-3"
                      disabled={busy}
                      value={mailbox}
                      onChange={(event) => {
                        setMailbox(event.target.value as typeof mailbox);
                        reset();
                      }}
                    >
                      <option value="outlook">Outlook / Hotmail</option>
                      <option value="gmail">Gmail</option>
                      <option value="mail">{t("accountPool.onboarding.panel.mailboxGeneric")}</option>
                    </select>
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="onboarding-files">
                    {source === "auth_file"
                      ? t("accountPool.onboarding.panel.filesLabelAuthFile")
                      : t("accountPool.onboarding.panel.filesLabelOAuth")}
                  </Label>
                  <Input
                    key={`${source}-${supplier}-${mailbox}-${prepareMailbox}`}
                    id="onboarding-files"
                    type="file"
                    accept={fileFormat ? ".json,application/json" : ".json,.txt,application/json,text/plain"}
                    multiple={source === "auth_file"}
                    disabled={fileInputDisabled}
                    onChange={(event) => {
                      void readFiles(Array.from(event.target.files ?? []));
                      event.target.value = "";
                    }}
                  />
                </div>
              </div>
              {source === "auth_file" && (
                <div className="grid gap-2 text-sm">
                  <label className="flex items-center gap-2">
                    <Checkbox
                      checked={useTemplate}
                      disabled={busy || !template}
                      onCheckedChange={(checked) => {
                        setUseTemplate(checked === true);
                        reset();
                      }}
                    />
                    {t("accountPool.onboarding.panel.useDefaultTemplate")}
                  </label>
                  <p className="text-xs text-muted-foreground">
                    {t("accountPool.onboarding.panel.templateHint")}
                    {!fileFormat && t("accountPool.onboarding.panel.templateHint2faSuffix")}
                  </p>
                  {useTemplate && template && (
                    <p className="text-xs text-muted-foreground">
                      {t("accountPool.onboarding.panel.templatePreview", {
                        priority: template.priority ?? t("accountPool.onboarding.panel.templateKeep"),
                        prefix:
                          template.prefix === ""
                            ? t("accountPool.onboarding.panel.templateClear")
                            : template.prefix ?? t("accountPool.onboarding.panel.templateKeep"),
                        proxy: templateProxyLabel(),
                      })}
                    </p>
                  )}
                  {missingTemplateProxy && (
                    <p role="alert" className="text-sm text-destructive">
                      {t("accountPool.onboarding.panel.templateProxyMissingError")}
                    </p>
                  )}
                  {settingsQuery.isError && (
                    <p role="status" className="text-xs text-destructive">
                      {t("accountPool.onboarding.panel.templateLoadError")}
                    </p>
                  )}
                </div>
              )}
              {source === "oauth" && (
                <div className="rounded-lg bg-muted/40 p-4 text-sm space-y-3">
                  <p>{t("accountPool.onboarding.panel.oauthMailboxHint")}</p>
                  <Label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={prepareMailbox}
                      disabled={busy}
                      onChange={(event) => {
                        setPrepareMailbox(event.target.checked);
                        reset();
                      }}
                    />
                    {t("accountPool.onboarding.panel.prepareMailboxLabel")}
                  </Label>
                  <details>
                    <summary className="cursor-pointer">{t("accountPool.onboarding.panel.viewAccountFormat")}</summary>
                    <pre className="mt-2 overflow-auto text-xs">
                      {t("accountPool.onboarding.panel.accountFormatSample")}
                    </pre>
                    <p className="mt-2">{t("accountPool.onboarding.panel.accountFormatTxtHint")}</p>
                  </details>
                </div>
              )}
              {suppliersQuery.isError && (
                <p role="alert" className="text-sm text-destructive">
                  {t("accountPool.onboarding.panel.suppliersLoadError")}
                </p>
              )}
              <p className="text-xs text-muted-foreground">{t("accountPool.onboarding.panel.catalogSyncHint")}</p>
              <p className="text-sm text-muted-foreground">{t("accountPool.onboarding.panel.defaultConfigHint")}</p>
              {preview.length > 0 && (
                <div className="space-y-3">
                  <p className="text-sm font-medium">
                    {t("accountPool.onboarding.panel.previewSummary", {
                      valid: preview.filter((item) => item.status === "valid").length,
                      duplicate: preview.filter((item) => item.status === "duplicate").length,
                      invalid: preview.filter((item) => item.status === "invalid").length,
                    })}
                  </p>
                  {rotationProfiles !== null && request && (
                    <details open className="rounded-lg border p-3 text-sm">
                      <summary className="cursor-pointer font-medium">
                        {t("accountPool.onboarding.panel.proxyAssignmentSummary")}
                      </summary>
                      <ul className="mt-2 grid max-h-40 gap-1 overflow-auto text-xs text-muted-foreground">
                        {request.entries.map((entry, index) => (
                          <li key={index} className="break-words">
                            {entry.label} →{" "}
                            {proxyProfiles.find((profile) => profile.id === entry.proxy_profile_id)?.name ??
                              t("accountPool.onboarding.panel.proxyUnavailable")}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <div className="max-h-64 overflow-auto rounded-lg border">
                    <table className="w-full text-left text-sm">
                      <thead className="bg-muted/50">
                        <tr>
                          <th className="p-3">{t("accountPool.onboarding.panel.colFileAccount")}</th>
                          <th>{t("accountPool.onboarding.panel.colCheckResult")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.map((item) => (
                          <tr key={item.index} className="border-t">
                            <td className="p-3 break-all">{item.label}</td>
                            <td className="p-3">{item.message}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Button
                    disabled={busy || !preview.some((item) => item.status === "valid")}
                    onClick={() => void submit()}
                  >
                    {busy
                      ? t("accountPool.onboarding.panel.submitting")
                      : t("accountPool.onboarding.panel.importValidItems")}
                  </Button>
                  <Button variant="outline" disabled={busy} className="ml-2" onClick={reset}>
                    {t("accountPool.onboarding.panel.cancelBatch")}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
      {source === "oauth" && <AccountPoolOnboardingTarget accessToken={accessToken} supplier={supplier} />}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">
          {source === "oauth" ? t("accountPool.onboarding.panel.oauthTasksTitle") : fileTasksTitle}
        </h3>
        <div className="flex flex-wrap gap-2">
          <AccountPoolOnboardingCleanup key={source} accessToken={accessToken} source={source} onChange={refresh} />
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            <RefreshCw className="size-4" />
            {t("accountPool.onboarding.panel.refresh")}
          </Button>
        </div>
      </div>
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t("accountPool.onboarding.panel.tasksLoadError")}
        </p>
      )}
      {query.isLoading ? (
        <p>{t("accountPool.onboarding.panel.tasksLoading")}</p>
      ) : (
        <AccountPoolOnboardingTasks
          key={source}
          accessToken={accessToken}
          items={(query.data ?? []).filter((item) => item.source === source)}
          onChange={refresh}
        />
      )}
    </div>
  );
}
