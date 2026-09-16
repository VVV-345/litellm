/** 本文件展示凭据文件的脱敏状态和所属卡片，不返回任何令牌或完整配置。 */

import { Download, FileKey2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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
import { toast } from "@/lib/toast";

import { formatDateTime } from "./AccountPoolFormatters";
import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import { AccountPoolSupplierLogo } from "./AccountPoolSupplierLogo";
import {
  deleteAccountPoolAuthFile,
  deleteAccountPoolCredential,
  downloadAccountPoolAuthFile,
  getAccountPoolAuthFileRefreshStatus,
  listAccountPoolCredentials,
  patchAccountPoolAuthFileStatus,
  patchAccountPoolAuthFileFields,
  refreshAccountPoolAuthFiles,
  setAccountPoolAuthFileRefreshInterval,
  type AccountPoolAuthFileRefreshStatus,
  uploadAccountPoolAuthFile,
} from "./AccountPoolManagementApi";

export const AccountPoolCredentialsPanel = ({
  accessToken,
  environments,
}: {
  accessToken: string | null;
  environments: readonly AccountPoolEnvironment[];
}) => {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadCardId, setUploadCardId] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [editCredential, setEditCredential] = useState<(typeof credentials)[number] | null>(null);
  const [editFields, setEditFields] = useState("{}");
  const query = useQuery({
    queryKey: ["account-pool", "credentials", accessToken],
    queryFn: () => listAccountPoolCredentials(accessToken!),
    enabled: accessToken !== null,
    retry: false,
  });
  const credentials = query.data ?? [];
  const refreshStatusQuery = useQuery({
    queryKey: ["account-pool", "auth-file-refresh", accessToken],
    queryFn: () => getAccountPoolAuthFileRefreshStatus(accessToken!),
    enabled: accessToken !== null,
    retry: false,
  });
  const refreshStatus = refreshStatusQuery.data;
  const refreshMutation = useMutation({
    mutationFn: () => refreshAccountPoolAuthFiles(accessToken!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "auth-file-refresh", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const intervalMutation = useMutation({
    mutationFn: (intervalMinutes: AccountPoolAuthFileRefreshStatus["interval_minutes"]) =>
      setAccountPoolAuthFileRefreshInterval(accessToken!, intervalMinutes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "auth-file-refresh", accessToken] });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!uploadCardId || uploadFile === null) throw new Error(t("accountPool.credentials.fileRequired"));
      return uploadAccountPoolAuthFile(accessToken!, uploadCardId, uploadFile);
    },
    onSuccess: () => {
      toast.success(t("accountPool.credentials.uploaded"));
      setUploadOpen(false);
      setUploadFile(null);
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const toggleMutation = useMutation({
    mutationFn: ({ environment, enabled }: { environment: AccountPoolEnvironment; enabled: boolean }) =>
      patchAccountPoolAuthFileStatus(accessToken!, environment.id, !enabled),
    onSuccess: () => {
      toast.success(t("accountPool.credentials.statusUpdated"));
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const downloadCredential = async (credential: (typeof credentials)[number]) => {
    try {
      const blob = await downloadAccountPoolAuthFile(accessToken!, credential.card_id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${credential.card_name}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.fromError(error);
    }
  };
  const deleteAuthFile = async (credential: (typeof credentials)[number]) => {
    if (!window.confirm(t("accountPool.credentials.removeConfirm"))) return;
    try {
      await deleteAccountPoolAuthFile(accessToken!, credential.card_id);
      toast.success(t("accountPool.credentials.removed"));
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    } catch (error) {
      toast.fromError(error);
    }
  };
  const saveAuthFileFields = async () => {
    if (!editCredential) return;
    let fields: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(editFields);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error("fields must be an object");
      fields = parsed as Record<string, unknown>;
    } catch (error) {
      toast.fromError(error);
      return;
    }
    try {
      await patchAccountPoolAuthFileFields(accessToken!, editCredential.card_id, fields);
      toast.success(t("accountPool.credentials.fieldsUpdated"));
      setEditCredential(null);
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    } catch (error) {
      toast.fromError(error);
    }
  };
  const removeCredential = (credential: (typeof credentials)[number]) => {
    const environment = environments.find((item) => item.id === credential.card_id);
    const index = Number.parseInt(credential.auth_index ?? "", 10) - 1;
    if (!environment || !Number.isInteger(index) || index < 0) return;
    if (!window.confirm(t("accountPool.credentials.removeConfirm"))) return;
    void deleteAccountPoolCredential(accessToken!, environment.id, {
      version: environment.version,
      credential_index: index,
    })
      .then(() => {
        toast.success(t("accountPool.credentials.removed"));
        void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
        void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
      })
      .catch((error: unknown) => toast.fromError(error));
  };
  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.credentials.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.credentials.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={String(refreshStatus?.interval_minutes ?? 15)}
            onValueChange={(value) =>
              intervalMutation.mutate(Number(value) as AccountPoolAuthFileRefreshStatus["interval_minutes"])
            }
            disabled={intervalMutation.isPending || accessToken === null}
          >
            <SelectTrigger className="w-40" aria-label={t("accountPool.credentials.refreshInterval")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {([5, 15, 30, 60] as const).map((minutes) => (
                <SelectItem key={minutes} value={String(minutes)}>
                  {t("accountPool.credentials.everyMinutes", { minutes })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
              onClick={() => {
                setUploadCardId(environments.find((environment) => environment.channel === "cliproxyapi")?.id ?? "");
                setUploadOpen(true);
              }}
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
          if (!open && !uploadMutation.isPending) {
            setUploadOpen(false);
            setUploadFile(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("accountPool.credentials.upload")}</DialogTitle>
            <DialogDescription>{t("accountPool.credentials.uploadDescription")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="grid gap-1">
              <Label>{t("accountPool.credentials.targetCard")}</Label>
              <select
                className="h-9 rounded-md border bg-background px-3 text-sm"
                value={uploadCardId}
                onChange={(event) => setUploadCardId(event.target.value)}
              >
                {environments
                  .filter((environment) => environment.channel === "cliproxyapi")
                  .map((environment) => (
                    <option key={environment.id} value={environment.id}>
                      {environment.name} · {t(`accountPool.supplier.${environment.supplier}`)}
                    </option>
                  ))}
              </select>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="account-pool-auth-file">{t("accountPool.credentials.file")}</Label>
              <Input
                id="account-pool-auth-file"
                type="file"
                accept=".json,.yaml,.yml"
                onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setUploadOpen(false)}
              disabled={uploadMutation.isPending}
            >
              {t("accountPool.cancel")}
            </Button>
            <Button
              type="button"
              onClick={() => uploadMutation.mutate()}
              disabled={uploadMutation.isPending || uploadFile === null || !uploadCardId}
            >
              {uploadMutation.isPending ? t("accountPool.credentials.uploading") : t("accountPool.credentials.upload")}
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
