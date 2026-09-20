/** 本文件集中号池环境的更新、授权和删除 mutation 编排。 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/http/client";
import { toast } from "@/lib/toast";

import {
  authorizeAccountPoolEnvironment,
  deleteAccountPoolEnvironment,
  listAccountPoolEnvironments,
  updateAccountPoolEnvironment,
} from "./AccountPoolApi";
import { canAuthorizeEnvironment, canDeleteEnvironment, canToggleEnvironment } from "./AccountPoolPermissions";
import { toUpdateRequest } from "./AccountPoolTypes";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";
import type { AccountPoolAuthorization, AccountPoolEnvironment } from "./AccountPoolTypes";

const isSavedWithPendingReconcile = (error: unknown): boolean =>
  error instanceof ApiError &&
  error.status === 503 &&
  /saved|保存/i.test(error.message) &&
  /gateway|网关|synchron|同步/i.test(error.message);

const GATEWAY_ERROR_STATUSES = [502, 503, 504] as const;
const DELETE_CONFIRMATION_INTERVAL_MS = 1000;
const DELETE_CONFIRMATION_ATTEMPTS = 45;

const isGatewayError = (error: unknown): error is ApiError =>
  error instanceof ApiError && GATEWAY_ERROR_STATUSES.some((status) => status === error.status);

const isHtmlGatewayError = (error: unknown): boolean => {
  if (!isGatewayError(error)) return false;
  const errorText = typeof error.body === "string" ? error.body : error.message;
  return /<!doctype html|<html/i.test(errorText);
};

const confirmEnvironmentDeleted = async (
  accessToken: string,
  environmentId: string,
  attemptsRemaining = DELETE_CONFIRMATION_ATTEMPTS,
): Promise<boolean> => {
  const environments = await listAccountPoolEnvironments(accessToken).catch(() => null);
  if (environments !== null && environments.every(({ id }) => id !== environmentId)) return true;
  if (attemptsRemaining <= 1) return false;
  await new Promise((resolve) => setTimeout(resolve, DELETE_CONFIRMATION_INTERVAL_MS));
  return confirmEnvironmentDeleted(accessToken, environmentId, attemptsRemaining - 1);
};

export const useAccountPoolMutations = (
  accessToken: string | null,
  canManage: boolean,
  onAuthorized: (authorization: AccountPoolAuthorization) => void,
  onDeleted: () => void,
) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot });
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
    mutationFn: async (environment: AccountPoolEnvironment) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canDeleteEnvironment(environment))
        throw new Error(t("accountPool.mutation.deleteUnavailable"));
      try {
        await deleteAccountPoolEnvironment(accessToken, environment.id);
      } catch (error) {
        if (!isGatewayError(error)) throw error;
        const deleted = await confirmEnvironmentDeleted(accessToken, environment.id);
        if (!deleted) throw error;
      }
    },
    onSuccess: () => {
      onDeleted();
      toast.success(t("accountPool.mutation.deleteRequested"));
      invalidate();
    },
    onError: (error: Error) => {
      if (isHtmlGatewayError(error)) {
        toast.error(t("accountPool.mutation.deleteFailed"));
      } else {
        toast.fromError(error);
      }
      invalidate();
    },
  });
  return { updateMutation, authorizeMutation, deleteMutation };
};
