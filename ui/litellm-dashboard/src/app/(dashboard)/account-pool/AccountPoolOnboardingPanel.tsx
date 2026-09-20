/** 在号池内展示独立的文件导入和 OAuth 上号工作流。 */
"use client";

import Link from "next/link";
import { migratedHref } from "@/utils/migratedPages";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileJson, Mail, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/lib/toast";
import {
  listOnboarding,
  listOnboardingSuppliers,
  parseMailboxAccounts,
  previewOnboarding,
  submitOnboarding,
  type OnboardingImport,
  type OnboardingPreview,
} from "./AccountPoolOnboardingApi";
import { AccountPoolOnboardingTasks } from "./AccountPoolOnboardingTasks";
import { AccountPoolOnboardingTarget } from "./AccountPoolOnboardingTarget";
import { accountPoolQueryKeys } from "./accountPoolQueryKeys";

export function AccountPoolOnboardingPanel({ accessToken }: { accessToken: string }) {
  const client = useQueryClient();
  const [source, setSource] = useState<"auth_file" | "oauth">("auth_file");
  const [supplier, setSupplier] = useState<OnboardingImport["supplier"]>("openai_codex");
  const [mailbox, setMailbox] = useState<"outlook" | "gmail" | "mail">("outlook");
  const [prepareMailbox, setPrepareMailbox] = useState(true);
  const [request, setRequest] = useState<OnboardingImport | null>(null);
  const [preview, setPreview] = useState<OnboardingPreview[]>([]);
  const [busy, setBusy] = useState(false);
  const suppliersQuery = useQuery({
    queryKey: accountPoolQueryKeys.onboardingSuppliers(accessToken),
    queryFn: () => listOnboardingSuppliers(accessToken),
  });
  const supported =
    suppliersQuery.data?.some(
      (item) => item.supplier === supplier && item[source === "oauth" ? "oauth" : "auth_file"],
    ) ?? false;
  const queryKey = accountPoolQueryKeys.onboarding(accessToken);
  const query = useQuery({ queryKey, queryFn: () => listOnboarding(accessToken), refetchInterval: 5000 });
  const reset = () => {
    setRequest(null);
    setPreview([]);
  };
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["account-pool"] });
  };
  const readFiles = async (files: File[]) => {
    reset();
    setBusy(true);
    try {
      if (!supported) throw new Error("当前供应商不支持此上号方式，请选择其他供应商");
      if (files.length === 0 || files.length > (source === "oauth" ? 1 : 100))
        throw new Error("认证文件每批最多 100 个，邮箱账号表每批选择一个文件");
      if (
        files.some((file) => file.size > 1024 * 1024) ||
        files.reduce((size, file) => size + file.size, 0) > 8 * 1024 * 1024
      ) {
        throw new Error("单文件最多 1 MiB，每批最多 8 MiB");
      }
      const entries =
        source === "auth_file"
          ? await Promise.all(
              files.map(async (file) => ({
                label: file.name,
                content: await file.text(),
                mailbox_password: "",
                supplier_password: "",
              })),
            )
          : parseMailboxAccounts(await files[0].text());
      const body: OnboardingImport = {
        job_id: crypto.randomUUID(),
        source,
        supplier,
        mailbox,
        prepare_mailbox: prepareMailbox,
        entries,
      };
      const checked = await previewOnboarding(accessToken, body);
      setRequest(body);
      setPreview(checked);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "文件检查失败");
    } finally {
      setBusy(false);
    }
  };
  const submit = async () => {
    if (!request) return;
    setBusy(true);
    try {
      const result = await submitOnboarding(accessToken, request);
      toast.success(`已接收 ${result.items.length} 项，任务进度会自动更新`);
      reset();
      await refresh();
    } catch {
      toast.error("提交未确认，请重试同一批次；系统会去重");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        供应商目录与卡片统一；仅支持 API Key 或服务账号的供应商，请到
        <Link href={migratedHref("account-pool?tab=providers")} className="ml-1 underline">
          提供商设置
        </Link>
        添加凭证。
      </p>
      <Tabs
        value={source}
        onValueChange={(value) => {
          if (value === "oauth" || value === "auth_file") {
            setSource(value);
            reset();
          }
        }}
      >
        <TabsList>
          <TabsTrigger value="auth_file">
            <FileJson className="size-4" />
            认证文件导入
          </TabsTrigger>
          <TabsTrigger value="oauth">
            <Mail className="size-4" />
            OAuth 上号
          </TabsTrigger>
        </TabsList>
        <TabsContent value={source}>
          <Card>
            <CardHeader>
              <CardTitle>{source === "auth_file" ? "批量导入认证文件" : "导入邮箱账号库存"}</CardTitle>
              <CardDescription>
                {source === "auth_file"
                  ? "选择供应商后批量上传 JSON，自动创建卡片、写入凭证并验证模型。此流程不发起 OAuth，也不进入邮箱账号表。"
                  : "邮箱账号与供应商账号分别保存。沿用现有 OAuth 授权，登录、验证码和二次验证由你完成，后台自动验证并更新卡片。"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="onboarding-supplier">模型供应商</Label>
                  <select
                    id="onboarding-supplier"
                    className="h-9 w-full rounded-md border bg-background px-3"
                    disabled={busy || suppliersQuery.isPending}
                    value={supplier}
                    onChange={(event) => {
                      setSupplier(event.target.value as OnboardingImport["supplier"]);
                      reset();
                    }}
                  >
                    {(suppliersQuery.data ?? []).map((item) => (
                      <option
                        key={item.supplier}
                        value={item.supplier}
                        disabled={!item[source === "oauth" ? "oauth" : "auth_file"]}
                      >
                        {item.display_name}
                        {item[source === "oauth" ? "oauth" : "auth_file"]
                          ? ""
                          : `（${item.authentication}，请在提供商添加）`}
                      </option>
                    ))}
                  </select>
                </div>
                {source === "oauth" && (
                  <div className="space-y-2">
                    <Label htmlFor="onboarding-mailbox">邮箱类型</Label>
                    <select
                      id="onboarding-mailbox"
                      className="h-9 w-full rounded-md border bg-background px-3"
                      disabled={busy}
                      value={mailbox}
                      onChange={(event) => {
                        setMailbox(event.target.value as typeof mailbox);
                        reset();
                      }}
                    >
                      <option value="outlook">Outlook / Hotmail</option>
                      <option value="gmail">Gmail</option>
                      <option value="mail">通用邮箱</option>
                    </select>
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="onboarding-files">
                    {source === "auth_file" ? "认证 JSON 文件（可多选）" : "账号 JSON 文件"}
                  </Label>
                  <Input
                    key={`${source}-${supplier}-${mailbox}-${prepareMailbox}`}
                    id="onboarding-files"
                    type="file"
                    accept=".json,application/json"
                    multiple={source === "auth_file"}
                    disabled={busy || !supported}
                    onChange={(event) => {
                      void readFiles(Array.from(event.target.files ?? []));
                      event.target.value = "";
                    }}
                  />
                </div>
              </div>
              {source === "oauth" && (
                <div className="rounded-lg bg-muted/40 p-4 text-sm space-y-3">
                  <p>
                    Outlook、Gmail
                    个人账号和通用邮箱暂由你完成改密。可以生成随机密码，改密成功后再确认保存。供应商密码与邮箱密码并不一定相同。
                  </p>
                  <Label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={prepareMailbox}
                      disabled={busy}
                      onChange={(event) => {
                        setPrepareMailbox(event.target.checked);
                        reset();
                      }}
                    />
                    导入后先准备邮箱并确认密码
                  </Label>
                  <details>
                    <summary className="cursor-pointer">查看账号文件格式</summary>
                    <pre className="mt-2 overflow-auto text-xs">
                      {
                        '[{"email":"you@example.com","mailbox_password":"邮箱密码","supplier_password":"可选的供应商密码"}]'
                      }
                    </pre>
                  </details>
                </div>
              )}
              {suppliersQuery.isError && (
                <p role="alert" className="text-sm text-destructive">
                  供应商目录读取失败，请刷新重试
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                目录与提供商模块保持一致。灰色项使用其他认证方式，可在提供商中添加。
              </p>
              <p className="text-sm text-muted-foreground">
                新卡片沿用现有全局默认配置。命名：供应商-套餐-账号；套餐读取不到时标记“套餐待识别”。
              </p>
              {preview.length > 0 && (
                <div className="space-y-3">
                  <p className="text-sm font-medium">
                    可导入 {preview.filter((item) => item.status === "valid").length} · 重复{" "}
                    {preview.filter((item) => item.status === "duplicate").length} · 无效{" "}
                    {preview.filter((item) => item.status === "invalid").length}
                  </p>
                  <div className="max-h-64 overflow-auto rounded-lg border">
                    <table className="w-full text-left text-sm">
                      <thead className="bg-muted/50">
                        <tr>
                          <th className="p-3">文件 / 账号</th>
                          <th>检查结果</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.map((item) => (
                          <tr key={item.index} className="border-t">
                            <td className="p-3 break-all">{item.label}</td>
                            <td className="p-3">{item.message}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Button
                    disabled={busy || !preview.some((item) => item.status === "valid")}
                    onClick={() => void submit()}
                  >
                    {busy ? "处理中…" : "导入有效项"}
                  </Button>
                  <Button variant="outline" disabled={busy} className="ml-2" onClick={reset}>
                    取消本批次
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
      {source === "oauth" && <AccountPoolOnboardingTarget accessToken={accessToken} supplier={supplier} />}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{source === "oauth" ? "OAuth 账号汇总与任务" : "认证文件任务"}</h3>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          <RefreshCw className="size-4" />
          刷新
        </Button>
      </div>
      {query.isError && (
        <p role="alert" className="text-sm text-destructive">
          任务读取失败，请刷新重试
        </p>
      )}
      {query.isLoading ? (
        <p>正在读取任务…</p>
      ) : (
        <AccountPoolOnboardingTasks
          key={source}
          accessToken={accessToken}
          items={(query.data ?? []).filter((item) => item.source === source)}
          onChange={refresh}
        />
      )}
    </div>
  );
}
