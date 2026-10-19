/** 本文件展示凭据文件的脱敏状态和所属卡片，不返回任何令牌或完整配置。 */

import { Download, FileKey2, FileUp, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { extractProxyErrorMessage } from "@/lib/http/client";

import { formatDateTime } from "../../utils/AccountPoolFormatters";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";
import { AccountPoolSupplierLogo } from "../providers/AccountPoolSupplierLogo";
import { useAccountPoolCredentials } from "../../hooks/useAccountPoolCredentials";
import { AccountPoolRefreshIntervalSelect } from "../shared/AccountPoolRefreshIntervalSelect";
import { AccountPoolAuthFileFieldsDialog } from "./AccountPoolAuthFileFieldsDialog";

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
    invalidateCredentials,
    removeCredential,
  } = useAccountPoolCredentials(accessToken, environments);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [selection, setSelection] = useState<{ token: string | null; ids: readonly string[] }>({
    token: accessToken,
    ids: [],
  });
  const selectedIds = selection.token === accessToken ? selection.ids : [];
  const [fieldsDialog, setFieldsDialog] = useState<{
    mode: "template" | "apply";
    targets: readonly { id: string; name: string }[];
  } | null>(null);
  const filtered = useMemo(
    () =>
      credentials.filter((credential) => {
        const matchesState =
          stateFilter === "all" || (stateFilter === "enabled" ? credential.enabled : !credential.enabled);
        const text = [
          credential.card_name,
          credential.account_email,
          credential.account_id,
          credential.file_name,
          credential.supplier,
        ]
          .join(" ")
          .toLocaleLowerCase();
        return matchesState && text.includes(search.trim().toLocaleLowerCase());
      }),
    [credentials, search, stateFilter],
  );
  const pageCount = Math.max(1, Math.ceil(filtered.length / 20));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * 20, currentPage * 20);
  const editable = (credential: (typeof credentials)[number]) =>
    credential.kind === "oauth_file" &&
    environments.some((environment) => environment.id === credential.card_id && environment.status !== "deleting");
  const selected = credentials.filter((credential) => selectedIds.includes(credential.id) && editable(credential));
  const select = (id: string, checked: boolean) =>
    setSelection({
      token: accessToken,
      ids: checked ? [...selectedIds.filter((item) => item !== id), id] : selectedIds.filter((item) => item !== id),
    });
  const visibleEditable = visible.filter(editable);

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{t("accountPool.credentials.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.credentials.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={accessToken === null}
            onClick={() => setFieldsDialog({ mode: "template", targets: [] })}
          >
            认证模板
          </Button>
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
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid min-w-0 flex-1 gap-1 sm:min-w-64">
          <Label htmlFor="credential-search">搜索认证文件</Label>
          <Input
            id="credential-search"
            type="search"
            placeholder="卡片、账号或文件名"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="credential-state">认证状态筛选</Label>
          <select
            id="credential-state"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            value={stateFilter}
            onChange={(event) => {
              setStateFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="all">全部状态</option>
            <option value="enabled">已启用</option>
            <option value="disabled">已停用</option>
          </select>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={selected.length === 0 || accessToken === null}
          onClick={() =>
            setFieldsDialog({
              mode: "apply",
              targets: selected.map((credential) => ({ id: credential.card_id, name: credential.card_name })),
            })
          }
        >
          批量设置（{selected.length}）
        </Button>
      </div>
      <div className="min-w-0 overflow-hidden rounded-lg border">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/30 px-4 py-2 text-sm">
          <label className="flex items-center gap-2">
            <Checkbox
              aria-label="全选本页认证文件"
              disabled={visibleEditable.length === 0 || accessToken === null}
              checked={
                visibleEditable.length > 0 && visibleEditable.every((credential) => selectedIds.includes(credential.id))
              }
              onCheckedChange={(checked) =>
                setSelection({
                  token: accessToken,
                  ids:
                    checked === true
                      ? [...new Set([...selectedIds, ...visibleEditable.map((credential) => credential.id)])]
                      : selectedIds.filter((id) => !visibleEditable.some((credential) => credential.id === id)),
                })
              }
            />
            全选本页
          </label>
          <span className="text-muted-foreground">
            共 {filtered.length} 项，已选 {selected.length} 项
          </span>
        </div>
        {query.isError && (
          <p role="alert" className="p-4">
            {t("accountPool.credentials.loadFailed")}
          </p>
        )}
        {query.isPending && <p className="p-4 text-sm text-muted-foreground">正在读取认证文件…</p>}
        {!query.isPending && !query.isError && filtered.length === 0 && (
          <p className="p-4 text-sm text-muted-foreground">没有匹配的认证文件</p>
        )}
        <ul className="divide-y">
          {visible.map((credential) => {
            const environment = environments.find((item) => item.id === credential.card_id);
            return (
              <li key={credential.id} className="grid min-w-0 gap-3 p-4 lg:grid-cols-[minmax(0,1fr)_auto]">
                <div className="flex min-w-0 items-start gap-3">
                  <Checkbox
                    aria-label={`选择 ${credential.card_name}`}
                    className="mt-1"
                    disabled={!editable(credential) || accessToken === null}
                    checked={selectedIds.includes(credential.id)}
                    onCheckedChange={(checked) => select(credential.id, checked === true)}
                  />
                  <div className="grid min-w-0 gap-1 text-sm">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <AccountPoolSupplierLogo supplier={credential.supplier} className="size-5 shrink-0" />
                      <FileKey2 className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 break-all font-medium">{credential.card_name}</span>
                      <Badge variant={credential.enabled ? "secondary" : "outline"}>{credential.status}</Badge>
                    </div>
                    <span className="break-all">
                      {credential.account_email ||
                        credential.account_id ||
                        t("accountPool.credentials.identityUnknown")}
                    </span>
                    {credential.account_email && credential.account_id && (
                      <span className="break-all text-xs text-muted-foreground">{credential.account_id}</span>
                    )}
                    {credential.file_name && (
                      <span className="break-all text-xs text-muted-foreground">{credential.file_name}</span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {t(`accountPool.supplier.${credential.supplier}`)} · {t("accountPool.credentials.models")}{" "}
                      {credential.model_count} · {credential.kind}
                    </span>
                    {credential.last_error && (
                      <p role="alert" className="break-words text-xs text-destructive">
                        {credential.last_error}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 self-start">
                  {credential.kind === "api_key" && (
                    <Button type="button" variant="ghost" size="sm" onClick={() => removeCredential(credential)}>
                      <Trash2 />
                      {t("accountPool.credentials.remove")}
                    </Button>
                  )}
                  {credential.kind === "oauth_file" && environment && (
                    <>
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
                        disabled={toggleMutation.isPending || accessToken === null || environment.status === "deleting"}
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
                        disabled={accessToken === null}
                        onClick={() => void downloadCredential(credential)}
                      >
                        <Download />
                        {t("accountPool.credentials.download")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!editable(credential) || accessToken === null}
                        onClick={() =>
                          setFieldsDialog({
                            mode: "apply",
                            targets: [{ id: credential.card_id, name: credential.card_name }],
                          })
                        }
                      >
                        {t("accountPool.credentials.editFields")}
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        size="sm"
                        disabled={accessToken === null || environment.status === "deleting"}
                        onClick={() => void deleteAuthFile(credential)}
                      >
                        <Trash2 />
                        {t("accountPool.credentials.remove")}
                      </Button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-between gap-3 border-t px-4 py-2 text-sm">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={currentPage <= 1}
            onClick={() => setPage(currentPage - 1)}
          >
            上一页
          </Button>
          <span>
            第 {currentPage} / {pageCount} 页
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={currentPage >= pageCount}
            onClick={() => setPage(currentPage + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
      <Dialog
        open={uploadOpen}
        onOpenChange={(open) => {
          if (!open) closeUpload();
        }}
      >
        <DialogContent
          className="max-h-[calc(100dvh-2rem)] min-w-0 overflow-y-auto sm:max-w-lg"
          showCloseButton={!uploadMutation.isPending}
        >
          <DialogHeader className="min-w-0">
            <DialogTitle>
              {t(replaceCredential ? "accountPool.credentials.replace" : "accountPool.credentials.upload")}
            </DialogTitle>
            <DialogDescription className="break-words">
              {t(
                replaceCredential
                  ? "accountPool.credentials.replaceDescription"
                  : "accountPool.credentials.uploadDescription",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="grid min-w-0 gap-3">
            <div className="grid min-w-0 gap-1">
              <Label htmlFor="account-pool-auth-target">{t("accountPool.credentials.targetCard")}</Label>
              {replaceCredential ? (
                <p className="min-w-0 break-all rounded-md border bg-muted/20 px-3 py-2 text-sm">
                  {replaceCredential.card_name}
                </p>
              ) : (
                <>
                  <select
                    id="account-pool-auth-target"
                    className="h-9 w-full min-w-0 rounded-md border bg-background px-3 text-sm"
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
                </>
              )}
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
      {fieldsDialog && accessToken && (
        <AccountPoolAuthFileFieldsDialog
          key={`${accessToken}-${fieldsDialog.mode}-${fieldsDialog.targets.map((target) => target.id).join(",")}`}
          accessToken={accessToken}
          mode={fieldsDialog.mode}
          targets={fieldsDialog.targets}
          onClose={() => setFieldsDialog(null)}
          onSaved={(ids) => {
            invalidateCredentials();
            setSelection({
              token: accessToken,
              ids: selectedIds.filter(
                (id) => !credentials.some((credential) => credential.id === id && ids.includes(credential.card_id)),
              ),
            });
          }}
        />
      )}
    </div>
  );
};
