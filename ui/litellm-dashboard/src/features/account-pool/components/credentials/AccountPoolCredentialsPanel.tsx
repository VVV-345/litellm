/** 本文件展示凭据文件的脱敏状态和所属卡片，不返回任何令牌或完整配置。 */

import { Download, FileKey2, FileUp, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { extractProxyErrorMessage } from "@/lib/http/client";

import { formatDateTime } from "../../utils/AccountPoolFormatters";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";
import { useAccountPoolCredentials } from "../../hooks/useAccountPoolCredentials";
import { AccountPoolRefreshIntervalSelect } from "../shared/AccountPoolRefreshIntervalSelect";

export const AccountPoolCredentialsPanel = ({
  accessToken,
  environments,
}: {
  accessToken: string | null;
  environments: readonly AccountPoolEnvironment[];
}) => {
  const { t, i18n } = useTranslation();
  const uploadInput = useRef<HTMLInputElement>(null);
  const {
    query,
    credentials,
    refreshStatus,
    refreshMutation,
    intervalMutation,
    uploadOpen,
    uploadCardId,
    setUploadCardId,
    uploadFile,
    setUploadFile,
    replaceCredential,
    uploadTargets,
    uploadMutation,
    openUpload,
    closeUpload,
    toggleMutation,
    downloadCredential,
    deleteAuthFile,
    editCredential,
    setEditCredential,
    editFields,
    setEditFields,
    saveAuthFileFields,
    removeCredential,
  } = useAccountPoolCredentials(accessToken, environments);
  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.credentials.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.credentials.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AccountPoolRefreshIntervalSelect
            value={refreshStatus?.interval_minutes ?? 15}
            onChange={(value) => intervalMutation.mutate(value)}
            disabled={intervalMutation.isPending || accessToken === null}
            label={t("accountPool.credentials.refreshInterval")}
            formatOption={(minutes) => t("accountPool.credentials.everyMinutes", { minutes })}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending || accessToken === null}
          >
            <RefreshCw className={refreshMutation.isPending || refreshStatus?.running ? "animate-spin" : undefined} />
            {t("accountPool.credentials.refresh")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw className={query.isFetching ? "animate-spin" : undefined} />
            {t("accountPool.refresh")}
          </Button>
          {environments.some((environment) => environment.channel === "cliproxyapi") && (
            <Button
              type="button"
              size="sm"
              disabled={uploadMutation.isPending || accessToken === null || query.isPending || query.isError}
              onClick={() => openUpload(null)}
            >
              <Plus />
              {t("accountPool.credentials.upload")}
            </Button>
          )}
        </div>
      </div>
      {refreshStatus && (
        <div className="grid gap-3 rounded-md border bg-muted/20 p-3 text-sm sm:grid-cols-2">
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.credentials.lastRefresh")}</p>
            <p className="mt-1 font-medium">{formatDateTime(refreshStatus.last_completed_at, i18n.language)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">{t("accountPool.credentials.nextRefresh")}</p>
            <p className="mt-1 font-medium">{formatDateTime(refreshStatus.next_refresh_at, i18n.language)}</p>
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {query.isError && <p role="alert">{t("accountPool.credentials.loadFailed")}</p>}
        {credentials.length === 0 && !query.isError && environments.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("accountPool.dashboard.empty")}</p>
        )}
        {credentials.map((credential) => (
          <Card key={credential.id}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <AccountPoolSupplierLogo supplier={credential.supplier} className="size-5 shrink-0" />
                  <FileKey2 className="size-4 shrink-0 text-muted-foreground" />
                  <CardTitle className="truncate text-base">{credential.card_name}</CardTitle>
                </div>
                <Badge variant={credential.enabled ? "secondary" : "outline"}>{credential.status}</Badge>
              </div>
            </CardHeader>
            <CardContent className="grid divide-y text-sm">
              <div className="grid gap-1 py-2">
                <span className="text-muted-foreground">{t("accountPool.credentials.accountIdentity")}</span>
                <span className="break-all">
                  {credential.account_email || credential.account_id || t("accountPool.credentials.identityUnknown")}
                </span>
                {credential.account_email && credential.account_id && (
                  <span className="break-all text-xs text-muted-foreground">{credential.account_id}</span>
                )}
                {credential.file_name && (
                  <span className="break-all text-xs text-muted-foreground">{credential.file_name}</span>
                )}
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <span className="text-muted-foreground">{t("accountPool.credentials.provider")}</span>
                <span>{t(`accountPool.supplier.${credential.supplier}`)}</span>
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <span className="text-muted-foreground">{t("accountPool.credentials.models")}</span>
                <span>{credential.model_count}</span>
              </div>
              <div className="flex items-center justify-between gap-3 py-2">
                <span className="text-muted-foreground">{t("accountPool.credentials.kind")}</span>
                <span>{credential.kind}</span>
              </div>
              {credential.kind === "api_key" && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="mt-2 justify-self-start"
                  onClick={() => removeCredential(credential)}
                >
                  <Trash2 />
                  {t("accountPool.credentials.remove")}
                </Button>
              )}
              {credential.kind === "oauth_file" &&
                (() => {
                  const environment = environments.find((item) => item.id === credential.card_id);
                  return environment ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={uploadMutation.isPending || accessToken === null || environment.status === "deleting"}
                        onClick={() => openUpload(credential)}
                      >
                        <FileUp />
                        {t("accountPool.credentials.replace")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={toggleMutation.isPending}
                        onClick={() => toggleMutation.mutate({ environment, enabled: !credential.enabled })}
                      >
                        {credential.enabled
                          ? t("accountPool.credentials.disable")
                          : t("accountPool.credentials.enable")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void downloadCredential(credential)}
                      >
                        <Download />
                        {t("accountPool.credentials.download")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setEditCredential(credential);
                          setEditFields("{}");
                        }}
                      >
                        {t("accountPool.credentials.editFields")}
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        onClick={() => void deleteAuthFile(credential)}
                      >
                        <Trash2 />
                        {t("accountPool.credentials.remove")}
                      </Button>
                    </div>
                  ) : null;
                })()}
              <p className="text-xs text-muted-foreground">{t("accountPool.credentials.secretHint")}</p>
              {credential.last_error && (
                <p role="alert" className="break-words text-xs text-destructive">
                  {credential.last_error}
                </p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <Dialog
        open={uploadOpen}
        onOpenChange={(open) => {
          if (!open) closeUpload();
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t(replaceCredential ? "accountPool.credentials.replace" : "accountPool.credentials.upload")}
            </DialogTitle>
            <DialogDescription>
              {t(
                replaceCredential
                  ? "accountPool.credentials.replaceDescription"
                  : "accountPool.credentials.uploadDescription",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="grid gap-1">
              <Label htmlFor="account-pool-auth-target">{t("accountPool.credentials.targetCard")}</Label>
              <select
                id="account-pool-auth-target"
                className="h-9 rounded-md border bg-background px-3 text-sm"
                value={uploadCardId}
                disabled={replaceCredential !== null || uploadMutation.isPending}
                onChange={(event) => setUploadCardId(event.target.value)}
              >
                <option value="" disabled>
                  {t("accountPool.credentials.selectTarget")}
                </option>
                {environments
                  .filter((environment) => environment.channel === "cliproxyapi")
                  .map((environment) => (
                    <option
                      key={environment.id}
                      value={environment.id}
                      disabled={
                        replaceCredential === null && !uploadTargets.some((target) => target.id === environment.id)
                      }
                    >
                      {environment.name} · {t(`accountPool.supplier.${environment.supplier}`)}
                      {credentials.some((credential) => credential.card_id === environment.id)
                        ? ` · ${t("accountPool.credentials.bound")}`
                        : ""}
                    </option>
                  ))}
              </select>
              {replaceCredential === null && (
                <p className="text-xs text-muted-foreground">{t("accountPool.credentials.alreadyBound")}</p>
              )}
            </div>
            {replaceCredential && (
              <p className="break-all text-sm text-muted-foreground">
                {t("accountPool.credentials.currentFile", {
                  name: replaceCredential.file_name || t("accountPool.credentials.identityUnknown"),
                })}
              </p>
            )}
            <div className="grid gap-1">
              <Label htmlFor="account-pool-auth-file">{t("accountPool.credentials.file")}</Label>
              <Input
                ref={uploadInput}
                id="account-pool-auth-file"
                type="file"
                className="sr-only"
                accept=".json"
                disabled={uploadMutation.isPending}
                onChange={(event) => {
                  setUploadFile(event.target.files?.[0] ?? null);
                  uploadMutation.reset();
                }}
              />
              <Button
                type="button"
                variant="outline"
                className="justify-self-start"
                disabled={uploadMutation.isPending}
                onClick={() => uploadInput.current?.click()}
              >
                <FileUp />
                {t("accountPool.credentials.chooseFile")}
              </Button>
              {uploadFile && <p className="min-w-0 break-all text-sm">{uploadFile.name}</p>}
            </div>
            {uploadMutation.isError && (
              <p role="alert" className="text-sm text-destructive">
                {extractProxyErrorMessage(uploadMutation.error)}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeUpload} disabled={uploadMutation.isPending}>
              {t("accountPool.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => uploadMutation.mutate()}
              disabled={
                uploadMutation.isPending ||
                uploadFile === null ||
                !uploadCardId ||
                (replaceCredential === null && !uploadTargets.some((environment) => environment.id === uploadCardId))
              }
            >
              {uploadMutation.isPending
                ? t("accountPool.credentials.uploading")
                : t(replaceCredential ? "accountPool.credentials.confirmReplace" : "accountPool.credentials.upload")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={editCredential !== null} onOpenChange={(open) => !open && setEditCredential(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("accountPool.credentials.editFields")}</DialogTitle>
            <DialogDescription>{editCredential?.card_name}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="account-pool-auth-fields">{t("accountPool.credentials.fieldsJson")}</Label>
            <Textarea
              id="account-pool-auth-fields"
              value={editFields}
              onChange={(event) => setEditFields(event.target.value)}
              rows={8}
              className="font-mono text-xs"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setEditCredential(null)}>
              {t("accountPool.cancel")}
            </Button>
            <Button type="button" onClick={() => void saveAuthFileFields()}>
              {t("accountPool.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};
