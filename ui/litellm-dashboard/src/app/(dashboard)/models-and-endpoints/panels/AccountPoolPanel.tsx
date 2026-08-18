// 本文件是 LiteLLM Admin UI 的号池入口，负责装配渠道表单和已同步账号列表。
"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import NotificationsManager from "@/components/molecules/notifications_manager";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import ProviderSourceForm from "../account-pool/ProviderSourceForm";
import { deletePoolAccount, listPoolAccounts, listProviderServices } from "../account-pool/api";
import type { PoolAccount } from "../account-pool/types";

export default function AccountPoolPanel() {
  const { accessToken } = useAuthorized();
  const poolQuery = useQuery({
    queryKey: ["account-pool", accessToken],
    enabled: Boolean(accessToken),
    queryFn: async () => {
      if (!accessToken) return { manifests: [], accounts: [] };
      const [manifests, accounts] = await Promise.all([
        listProviderServices(accessToken),
        listPoolAccounts(accessToken),
      ]);
      return { manifests, accounts };
    },
  });
  const deleteMutation = useMutation({
    mutationFn: async (accountId: string) => {
      if (!accessToken) throw new Error("未获得管理员令牌");
      return deletePoolAccount(accessToken, accountId);
    },
    onSuccess: async (result) => {
      if (!result.ok) {
        NotificationsManager.fromBackend(result.message);
        return;
      }
      NotificationsManager.success(result.message);
      await poolQuery.refetch();
    },
    onError: () => NotificationsManager.fromBackend("删除渠道失败，请检查号池服务"),
  });

  const manifests = poolQuery.data?.manifests ?? [];
  const accounts = poolQuery.data?.accounts ?? [];
  const refresh = async () => {
    await poolQuery.refetch();
  };

  if (!accessToken) return null;

  return (
    <div className="space-y-8">
      <section>
        <ProviderFormState
          accessToken={accessToken}
          manifests={manifests}
          loading={poolQuery.isLoading}
          onCreated={refresh}
        />
      </section>

      <section className="border-t pt-6">
        <div className="mb-3 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold">已同步渠道</h3>
            <p className="text-sm text-muted-foreground">Key 仅写入 LiteLLM，号池只显示运行状态和模型映射</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={poolQuery.isFetching}
            onClick={refresh}
            title="刷新号池渠道"
            aria-label="刷新号池渠道"
          >
            <RefreshCw className={poolQuery.isFetching ? "animate-spin" : ""} />
          </Button>
        </div>
        <AccountTable
          accounts={accounts}
          deleting={deleteMutation.isPending}
          onDelete={(accountId) => deleteMutation.mutate(accountId)}
        />
      </section>
    </div>
  );
}

function ProviderFormState({
  accessToken,
  manifests,
  loading,
  onCreated,
}: {
  accessToken: string;
  manifests: Awaited<ReturnType<typeof listProviderServices>>;
  loading: boolean;
  onCreated: () => Promise<void>;
}) {
  if (manifests.length > 0) {
    return <ProviderSourceForm accessToken={accessToken} manifests={manifests} onCreated={onCreated} />;
  }
  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="animate-spin" /> 正在加载渠道服务
      </div>
    );
  }
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
      无法连接号池服务，请确认 account-pool 已启动并配置内部令牌。
    </div>
  );
}

function AccountTable({
  accounts,
  deleting,
  onDelete,
}: {
  accounts: PoolAccount[];
  deleting: boolean;
  onDelete: (accountId: string) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>渠道</TableHead>
          <TableHead>分组</TableHead>
          <TableHead>供应商</TableHead>
          <TableHead>模型</TableHead>
          <TableHead>运行状态</TableHead>
          <TableHead className="w-16">操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {accounts.length === 0 && (
          <TableRow>
            <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
              暂无已同步渠道
            </TableCell>
          </TableRow>
        )}
        {accounts.map((account) => (
          <TableRow key={account.id}>
            <TableCell className="font-medium">{account.display_name}</TableCell>
            <TableCell>{account.group || "-"}</TableCell>
            <TableCell>{account.provider}</TableCell>
            <TableCell>
              <div className="flex max-w-xl flex-wrap gap-1">
                {account.models.map((model) => (
                  <Badge key={model} variant="outline">
                    {model}
                  </Badge>
                ))}
              </div>
            </TableCell>
            <TableCell>
              {account.runtime.health} · {account.runtime.inflight}/{account.runtime.max_concurrency}
            </TableCell>
            <TableCell>
              <DeleteAccountButton account={account} deleting={deleting} onDelete={onDelete} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function DeleteAccountButton({
  account,
  deleting,
  onDelete,
}: {
  account: PoolAccount;
  deleting: boolean;
  onDelete: (accountId: string) => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={deleting}
            title={`删除 ${account.display_name}`}
            aria-label={`删除 ${account.display_name}`}
          >
            <Trash2 />
          </Button>
        }
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除渠道？</AlertDialogTitle>
          <AlertDialogDescription>
            将删除“{account.display_name}”及由号池创建的 LiteLLM Deployment，此操作无法撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => onDelete(account.id)}>
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
