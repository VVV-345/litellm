/** 本文件集中号池环境的更新、授权和删除 mutation 编排。 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

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
  const queryClient = useQueryClient();
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ACCOUNT_POOL_ENVIRONMENTS_QUERY_KEY });
  const updateMutation = useMutation({
    mutationFn: ({ environment, enabled }: { environment: AccountPoolEnvironment; enabled: boolean }) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canToggleEnvironment(environment)) throw new Error("当前状态不支持切换环境开关");
      return updateAccountPoolEnvironment(accessToken, environment.id, toUpdateRequest(environment, { enabled }));
    },
    onSuccess: () => {
      toast.success("环境开关已更新");
      invalidate();
    },
    onError: (error: Error) => {
      if (isSavedWithPendingReconcile(error)) {
        toast.warning("配置已保存，网关同步待完成", { description: "后台会继续重试同步，请稍后刷新状态" });
      } else {
        toast.fromError(error);
      }
      invalidate();
    },
  });
  const authorizeMutation = useMutation({
    mutationFn: (environment: AccountPoolEnvironment) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canAuthorizeEnvironment(environment)) throw new Error("当前状态不支持重新授权");
      return authorizeAccountPoolEnvironment(accessToken, environment.id);
    },
    onSuccess: (result) => {
      onAuthorized(result);
      invalidate();
      toast.success("已生成新的授权信息");
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const deleteMutation = useMutation({
    mutationFn: (environment: AccountPoolEnvironment) => {
      if (!accessToken) throw new Error("Access token required");
      if (!canManage || !canDeleteEnvironment(environment)) throw new Error("当前状态不支持删除");
      return deleteAccountPoolEnvironment(accessToken, environment.id);
    },
    onSuccess: () => {
      onDeleted();
      toast.success("环境删除请求已完成");
      invalidate();
    },
    onError: (error: Error) => {
      toast.fromError(error);
      invalidate();
    },
  });
  return { updateMutation, authorizeMutation, deleteMutation };
};
