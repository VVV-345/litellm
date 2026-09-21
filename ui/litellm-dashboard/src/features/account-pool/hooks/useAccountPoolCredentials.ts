/** 编排认证文件查询和操作，统一刷新卡片与凭据缓存，保留上传及替换的状态边界。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { toast } from "@/lib/toast";
import type { AccountPoolEnvironment } from "../utils/AccountPoolTypes";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
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
} from "../api/AccountPoolManagementApi";

export const useAccountPoolCredentials = (
  accessToken: string | null,
  environments: readonly AccountPoolEnvironment[],
) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidateCredentials = () => {
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.credentials(accessToken) });
    void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot });
  };
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadCardId, setUploadCardId] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [replaceCredential, setReplaceCredential] = useState<(typeof credentials)[number] | null>(null);
  const [editCredential, setEditCredential] = useState<(typeof credentials)[number] | null>(null);
  const [editFields, setEditFields] = useState("{}");
  const query = useQuery({
    queryKey: accountPoolQueryKeys.credentials(accessToken),
    queryFn: () => listAccountPoolCredentials(accessToken!),
    enabled: accessToken !== null,
    retry: false,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });
  const credentials = query.data ?? [];
  const uploadTargets = environments.filter(
    (environment) =>
      environment.channel === "cliproxyapi" &&
      environment.status !== "deleting" &&
      !credentials.some((credential) => credential.card_id === environment.id),
  );
  const refreshStatusQuery = useQuery({
    queryKey: accountPoolQueryKeys.authFileRefresh(accessToken),
    queryFn: () => getAccountPoolAuthFileRefreshStatus(accessToken!),
    enabled: accessToken !== null,
    retry: false,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });
  const refreshStatus = refreshStatusQuery.data;
  const refreshMutation = useMutation({
    mutationFn: () => refreshAccountPoolAuthFiles(accessToken!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.authFileRefresh(accessToken) });
      invalidateCredentials();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const intervalMutation = useMutation({
    mutationFn: (intervalMinutes: AccountPoolAuthFileRefreshStatus["interval_minutes"]) =>
      setAccountPoolAuthFileRefreshInterval(accessToken!, intervalMinutes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.authFileRefresh(accessToken) });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!uploadCardId || uploadFile === null) throw new Error(t("accountPool.credentials.fileRequired"));
      if (replaceCredential === null && !uploadTargets.some((environment) => environment.id === uploadCardId)) {
        throw new Error(t("accountPool.credentials.alreadyBound"));
      }
      return uploadAccountPoolAuthFile(accessToken!, uploadCardId, uploadFile, replaceCredential !== null);
    },
    onSuccess: () => {
      toast.success(t(replaceCredential ? "accountPool.credentials.replaced" : "accountPool.credentials.uploaded"));
      setUploadOpen(false);
      setUploadFile(null);
      setReplaceCredential(null);
    },
    onSettled: invalidateCredentials,
    onError: (error: Error) => toast.fromError(error),
  });
  const openUpload = (credential: (typeof credentials)[number] | null) => {
    uploadMutation.reset();
    setReplaceCredential(credential);
    setUploadFile(null);
    setUploadCardId(credential?.card_id ?? "");
    setUploadOpen(true);
  };
  const closeUpload = () => {
    if (uploadMutation.isPending) return;
    setUploadOpen(false);
    setUploadFile(null);
    setReplaceCredential(null);
  };
  const toggleMutation = useMutation({
    mutationFn: ({ environment, enabled }: { environment: AccountPoolEnvironment; enabled: boolean }) =>
      patchAccountPoolAuthFileStatus(accessToken!, environment.id, !enabled),
    onSuccess: () => {
      toast.success(t("accountPool.credentials.statusUpdated"));
      invalidateCredentials();
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
      invalidateCredentials();
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
      invalidateCredentials();
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
        invalidateCredentials();
      })
      .catch((error: unknown) => toast.fromError(error));
  };
  return {
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
  };
};
