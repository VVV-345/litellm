/** 本文件集中号池环境的更新、授权和删除 mutation 编排。 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/http/client";
import { toast } from "@/lib/toast";

import {
  authorizeAccountPoolEnvironment,
  deleteAccountPoolEnvironment,
  updateAccountPoolEnvironment,
} from "./AccountPoolApi";
import { canAuthorizeEnvironment, canDeleteEnvironment, canToggleEnvironment } from "./AccountPoolPermissions";
import { toUpdateRequest } from "./AccountPoolTypes";
import { ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY } from "./useAccountPoolQuery";
import type { AccountPoolAuthorization, AccountPoolEnvironment } from "./AccountPoolTypes";

const isSavedWithPendingReconcile = (error: unknown): boolean =>
  error instanceof ApiError &&
  error.status === 503 &&
  /saved|保存/i.test(error.message) &&
  /gateway|网关|synchron|同步/i.test(error.message);

export const useAccountPoolMutations = (
  accessToken: string | null,
  canManage: boolean,
  onAuthorized: (authorization: AccountPoolAuthorization) => void,
  onDeleted: () => void,
) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY });
  const updateMutation = useMutation({
    mutationFn: ({ environment, enabled }: { environment: AccountPoolEnvironment; enabled: boolean }) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canToggleEnvironment(environment))
        throw new Error(t("accountPool.mutation.toggleUnavailable"));
      return updateAccountPoolEnvironment(accessToken, environment.id, toUpdateRequest(environment, { enabled }));
    },
    onSuccess: () => {
      toast.success(t("accountPool.mutation.updated"));
      invalidate();
    },
    onError: (error: Error) => {
      if (isSavedWithPendingReconcile(error)) {
        toast.warning(t("accountPool.config.savedPendingSync"), {
          description: t("accountPool.config.savedPendingSyncDescription"),
        });
      } else {
        toast.fromError(error);
      }
      invalidate();
    },
  });
  const authorizeMutation = useMutation({
    mutationFn: (environment: AccountPoolEnvironment) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canAuthorizeEnvironment(environment)) {
        throw new Error(t("accountPool.mutation.reauthorizeUnavailable"));
      }
      return authorizeAccountPoolEnvironment(accessToken, environment.id);
    },
    onSuccess: (result) => {
      onAuthorized(result);
      invalidate();
      toast.success(t("accountPool.mutation.authorizationGenerated"));
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const deleteMutation = useMutation({
    mutationFn: (environment: AccountPoolEnvironment) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canDeleteEnvironment(environment))
        throw new Error(t("accountPool.mutation.deleteUnavailable"));
      return deleteAccountPoolEnvironment(accessToken, environment.id);
    },
    onSuccess: () => {
      onDeleted();
      toast.success(t("accountPool.mutation.deleteRequested"));
      invalidate();
    },
    onError: (error: Error) => {
      toast.fromError(error);
      invalidate();
    },
  });
  return { updateMutation, authorizeMutation, deleteMutation };
};
