/** 统一保存额度刷新档位并更新仪表盘与额度详情共享缓存。 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";
import {
  setAccountPoolQuotaRefreshInterval,
  type AccountPoolQuotaRefreshStatus,
} from "../api/AccountPoolManagementApi";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export function useAccountPoolQuotaInterval(accessToken?: string | null) {
  const client = useQueryClient();
  const { t } = useTranslation();
  return useMutation({
    mutationFn: (interval: AccountPoolQuotaRefreshStatus["interval_minutes"]) =>
      setAccountPoolQuotaRefreshInterval(accessToken!, interval),
    onSuccess: (status) => {
      client.setQueryData(accountPoolQueryKeys.quotaRefreshStatus(accessToken ?? null), status);
      toast.success(t("accountPool.quotas.intervalSaved"));
    },
    onError: (error) => toast.fromError(error),
  });
}
