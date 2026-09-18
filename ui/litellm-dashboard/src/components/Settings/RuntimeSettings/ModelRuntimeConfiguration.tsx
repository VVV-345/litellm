"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { canManageAccountPool } from "@/app/(dashboard)/account-pool/AccountPoolPermissions";
import { useAccountPoolQuery } from "@/app/(dashboard)/account-pool/useAccountPoolQuery";
import { listAccountPolicies } from "@/app/(dashboard)/account-pool/AccountPoolManagementApi";
import { Button } from "@/components/ui/button";
import { RuntimeConfigDialog } from "./RuntimeConfigDialog";
import { RuntimePolicyDialog } from "./RuntimePolicyDialog";

export default function ModelRuntimeConfiguration({
  accountId,
  initialPolicy = false,
}: {
  accountId: string;
  initialPolicy?: boolean;
}) {
  const { accessToken, userRole } = useAuthorized();
  const allowed = canManageAccountPool(userRole, false);
  const accounts = useAccountPoolQuery(accessToken, allowed, false);
  const policies = useQuery({
    queryKey: ["account-pool", "policies", accessToken],
    queryFn: () => listAccountPolicies(accessToken!),
    enabled: allowed && Boolean(accessToken),
    retry: false,
  });
  const [editing, setEditing] = useState<"runtime" | "policy" | null>(initialPolicy ? "policy" : null);
  if (!allowed || !accessToken) return null;
  if (accounts.isPending || policies.isPending) return <p>正在读取卡片配置…</p>;
  if (accounts.isError || policies.isError) return <p role="alert">卡片配置读取失败，请刷新重试</p>;
  const account = accounts.data?.find((item) => item.id === accountId);
  if (!account) return <p role="alert">卡片不存在或已删除</p>;
  return (
    <div className="my-4 space-y-3 rounded-lg border p-4">
      <p className="font-medium">{account.name} · 卡片配置</p>
      <p className="text-sm text-muted-foreground">
        同一卡片的模型共用授权、配额和出站代理。模型权限、护栏、预算和路由使用 LiteLLM 设置。
      </p>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setEditing("runtime")}>
          运行配置
        </Button>
        <Button variant="outline" onClick={() => setEditing("policy")}>
          账号策略
        </Button>
      </div>
      {editing === "runtime" && (
        <RuntimeConfigDialog
          key={account.id}
          accessToken={accessToken}
          environment={account}
          open
          onOpenChange={(open) => !open && setEditing(null)}
          onRefresh={() => void accounts.refetch()}
          onSaved={() => {
            setEditing(null);
            void accounts.refetch();
          }}
        />
      )}
      {editing === "policy" && (
        <RuntimePolicyDialog
          accessToken={accessToken}
          environment={account}
          environments={accounts.data ?? []}
          policies={policies.data ?? []}
          onOpenRuntimeConfig={() => setEditing("runtime")}
          onClose={() => {
            setEditing(null);
            void policies.refetch();
          }}
        />
      )}
    </div>
  );
}
