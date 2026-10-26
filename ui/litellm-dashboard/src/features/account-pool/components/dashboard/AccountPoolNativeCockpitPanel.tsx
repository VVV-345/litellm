"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { accountPoolBackendOptions, nativeCockpitAccountsOptions } from "../../hooks/accountPoolOptions";
import { AccountPoolCockpitPolicyPanel } from "./AccountPoolCockpitPolicyPanel";
import {
  changeNativeScope,
  nativeReceipt,
  preflightNativeCockpit,
  readNativeScope,
  type NativePreflight,
  type NativeScopeStatus,
} from "../../api/NativeCockpitApi";

export function AccountPoolBackendBoundary({ accessToken, children }: { accessToken: string; children: ReactNode }) {
  const backend = useQuery(accountPoolBackendOptions(accessToken));
  if (backend.isPending)
    return (
      <p role="status" className="p-6">
        正在读取号池后端…
      </p>
    );
  if (backend.isError)
    return (
      <div className="p-6">
        <p role="alert">号池后端读取失败，未尝试切回 Manager</p>
        <Button onClick={() => void backend.refetch()}>重新读取</Button>
      </div>
    );
  return backend.data.backend === "cockpit" ? (
    <AccountPoolNativeCockpitPanel key={accessToken} accessToken={accessToken} />
  ) : (
    children
  );
}

export function AccountPoolNativeCockpitPanel({ accessToken }: { accessToken: string }) {
  const accounts = useQuery(nativeCockpitAccountsOptions(accessToken));
  const [input, setInput] = useState("");
  const [subject, setSubject] = useState("");
  return (
    <main className="mx-auto w-full max-w-5xl space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold">LiteLLM + Cockpit 原生管理</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          凭据由 Cockpit 管理，用户与 Key 权限由 LiteLLM 主库核验；此流程不调用 Manager
        </p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>账号与凭据状态</CardTitle>
          <CardDescription>此处只确认凭据文件存在，不代表提供商登录或额度已验证</CardDescription>
        </CardHeader>
        <CardContent>
          {accounts.isPending && <p role="status">正在读取账号…</p>}
          {accounts.isError && <p role="alert">账号读取失败，请检查原生 Cockpit 配置</p>}
          {accounts.data?.length === 0 && <p>尚无账号，请先在 Cockpit 导入凭据并配置账号</p>}
          <ul className="space-y-2">
            {accounts.data?.map((account) => (
              <li key={account.account_id} className="flex flex-wrap justify-between gap-2 border-b py-2">
                <span className="break-all">{account.account_id}</span>
                <span>{account.credential_present ? "凭据文件存在" : "凭据文件缺失"}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>主体授权流程</CardTitle>
          <CardDescription>
            使用已由管理员绑定账号和明确模型范围的原生 Key。输入 64 位哈希，不要输入原始 API Key
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Label htmlFor="native-subject">原生 Key 哈希</Label>
          <div className="flex flex-wrap gap-2">
            <Input
              id="native-subject"
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setSubject("");
              }}
              className="min-w-0 flex-1"
              autoComplete="off"
              spellCheck={false}
            />
            <Button disabled={!/^[0-9a-f]{64}$/.test(input)} onClick={() => setSubject(input)}>
              选择主体
            </Button>
          </div>
          {subject && <NativeSubjectWorkflow key={subject} accessToken={accessToken} subject={subject} />}
        </CardContent>
      </Card>
      <AccountPoolCockpitPolicyPanel accessToken={accessToken} native />
    </main>
  );
}

type Action = "refresh" | "preflight" | "issue" | "revalidate" | "publish" | "activate" | "deactivate" | "revoke";
const states = { absent: "未发布", staged: "已发布，停用", active: "已启用", blocked: "已阻断" } as const;
const runtimeStates = {
  active: "运行态：已核对当前版本",
  unavailable: "运行态：不可用",
  pending: "运行态：待确认，不能视为已生效",
} as const;

function NativeScopeView({ view }: { view: NativeScopeStatus }) {
  return (
    <div className="space-y-2">
      <p>投影：{states[view.scope.state]}</p>
      <p>{runtimeStates[view.runtime_state]}</p>
      <p className="break-all text-xs">已保存版本：{view.scope.persisted_manifest_revision ?? "未知"}</p>
      <p className="break-all text-xs">运行版本：{view.runtime?.active_revision ?? "未知"}</p>
      <p className="text-sm">模型范围：{view.scope.models.join("、") || "无"}</p>
    </div>
  );
}

function NativeSubjectWorkflow({ accessToken, subject }: { accessToken: string; subject: string }) {
  const [view, setView] = useState<NativeScopeStatus | null>(null);
  const [preflight, setPreflight] = useState<NativePreflight | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [verified, setVerified] = useState(false);
  const operation = useMutation({
    retry: false,
    mutationFn: async (action: Action) => {
      if (action === "preflight") {
        setReceipt(null);
        setVerified(false);
        const result = await preflightNativeCockpit(accessToken, subject);
        setPreflight(result);
        if (result.status !== "eligible") throw new Error("预检未通过，请检查主体权限和 Cockpit 凭据");
      } else if (action === "issue" || action === "revalidate") {
        const result = await nativeReceipt(
          accessToken,
          subject,
          action === "revalidate" && receipt ? receipt : undefined,
        );
        if (result.status !== "native_scope_verified" || !result.receipt) throw new Error("回执校验未通过，请重新预检");
        setReceipt(result.receipt);
        setVerified(action === "revalidate");
      } else if (action !== "refresh") {
        const revision = view?.scope.persisted_manifest_revision;
        if (!revision) throw new Error("请先读取当前版本");
        const result = await changeNativeScope(accessToken, action, {
          subject_hash: subject,
          revision,
          ...(action === "publish" && receipt ? { receipt } : {}),
        });
        setReceipt(null);
        setVerified(false);
        setPreflight(null);
        setView(result);
        return;
      }
      setView(await readNativeScope(accessToken, subject));
    },
    onError: () => {
      setReceipt(null);
      setVerified(false);
      setPreflight(null);
      setView(null);
    },
  });
  const pending = operation.isPending;
  const run = (action: Action) => operation.mutate(action);
  return (
    <section className="space-y-4 border-t pt-4">
      <div className="flex flex-wrap gap-2">
        <Button disabled={pending} onClick={() => run("preflight")}>
          预检权限与凭据
        </Button>
        <Button variant="outline" disabled={pending || preflight?.status !== "eligible"} onClick={() => run("issue")}>
          签发回执
        </Button>
        <Button variant="outline" disabled={pending || !receipt} onClick={() => run("revalidate")}>
          重新验证回执
        </Button>
        <Button disabled={pending || !verified || !view} onClick={() => run("publish")}>
          发布停用投影
        </Button>
      </div>
      {preflight?.status === "eligible" && <p>预检通过</p>}
      {receipt && <p>{verified ? "回执重验通过" : "回执已签发，尚未重验"}</p>}
      {operation.isError && (
        <p role="alert" className="text-destructive">
          操作失败或结果无法确认，请重新读取并预检。不会自动重试写入
        </p>
      )}
      {pending && <p role="status">正在核验并执行…</p>}
      {view && <NativeScopeView view={view} />}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={pending} onClick={() => run("refresh")}>
          读取并确认运行版本
        </Button>
        <Button variant="outline" disabled={pending || view?.scope.state !== "staged"} onClick={() => run("activate")}>
          启用投影
        </Button>
        <Button
          variant="outline"
          disabled={pending || !view || !["active", "blocked"].includes(view.scope.state)}
          onClick={() => run("deactivate")}
        >
          停用投影
        </Button>
        <Button
          variant="destructive"
          disabled={pending || !view || view.scope.state === "absent"}
          onClick={() => run("revoke")}
        >
          撤销投影
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        scope 变更需由管理员在隔离环境重启 Cockpit 后再核对运行版本。这里不自动重启；版本不一致时原生请求会被阻断
      </p>
    </section>
  );
}
