/** 独立换绑入口：只在明确确认后修改账号，任务失败保留原卡和目标邮箱占用。 */
"use client";

import dynamic from "next/dynamic";
import { useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractProxyErrorMessage } from "@/lib/http/client";
import {
  actOnEmailBinding,
  confirmEmailBinding,
  exportEmailBindingStatus,
  getEmailBindings,
  openEmailBindingScreen,
  proposeEmailBinding,
  provideEmailBindingCode,
  uploadEmailPool,
  type BindingView,
} from "../../api/AccountPoolEmailRebindingApi";
import { createAccountPoolJobId, getAccountPoolOAuthBrowserUrl } from "../../api/AccountPoolManagementApi";
import { getAutomation } from "../../api/AccountPoolAutomationApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import type { AccountPoolEnvironment } from "../../utils/AccountPoolTypes";

function mailboxLabel(status: string) {
  if (status === "bound") return "已绑定";
  if (status === "reserved") return "处理中，不可重复分配";
  return "未使用";
}
function retryLabel(changed: boolean, stage: string) {
  if (changed) return "仅重试授权与验收";
  return stage === "waiting_code" ? "重试收信" : "重试准备浏览器";
}

const Scripts = dynamic(() =>
  import("../onboarding/AccountPoolLoginScriptSettings").then((m) => m.AccountPoolLoginScriptSettings),
);
const Authorization = dynamic(() =>
  import("../onboarding/AccountPoolAutomationPanel").then((m) => m.AccountPoolAutomationPanel),
);

export function AccountPoolEmailRebinding({
  accessToken,
  environment,
}: {
  accessToken: string | null;
  environment: AccountPoolEnvironment;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!accessToken}
        onClick={() => setOpen(true)}
        aria-label={`换绑邮箱 ${environment.name}`}
      >
        换绑邮箱
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>换绑邮箱 · {environment.name}</DialogTitle>
            <DialogDescription>
              已完成 OAuth
              才能准备换绑；已有任务即使授权失败也可在此继续。同一卡片重新授权、保存凭据并测试，上传文件不会开始换绑。
            </DialogDescription>
          </DialogHeader>
          {open && accessToken && <BindingPanel accessToken={accessToken} environment={environment} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function BindingPanel({
  accessToken,
  environment,
}: {
  accessToken: string;
  environment: AccountPoolEnvironment;
}) {
  const id = useId();
  const file = useRef<HTMLInputElement>(null);
  const client = useQueryClient();
  const key = accountPoolQueryKeys.emailRebinding(accessToken);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState("");
  const [scripts, setScripts] = useState(false);
  const [screen, setScreen] = useState<string | null>(null);
  const query = useQuery({
    queryKey: key,
    queryFn: () => getEmailBindings(accessToken),
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.jobs.some((j) => !["completed", "cancelled", "failed", "uncertain"].includes(j.stage))
        ? 2000
        : false,
  });
  const jobs = query.data?.jobs.filter((j) => j.environment_id === environment.id) ?? [];
  const job = jobs.at(-1);
  const pending = job && !["completed", "cancelled"].includes(job.stage);
  const available = query.data?.mailboxes.filter((m) => m.status === "available") ?? [];
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await client.invalidateQueries({ queryKey: key });
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.environments(accessToken) });
    } catch (failure) {
      setError(extractProxyErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const importFile = async () => {
    const selected = file.current?.files?.[0];
    if (!selected) return;
    if (selected.size > 1048576) {
      setError("文件不能超过 1 MiB");
      return;
    }
    await run(async () => {
      const result = await uploadEmailPool(accessToken, await selected.text(), selected.name);
      setNotice(`新增 ${result.added} 条，已存在 ${result.existing} 条；不会覆盖已绑定标记或轮换后的令牌`);
      if (file.current) file.current.value = "";
    });
  };
  return (
    <div className="space-y-4">
      <section className="space-y-2 rounded-lg border p-3">
        <Label htmlFor={`${id}-file`}>上传目标邮箱 CSV / TXT</Label>
        <Input id={`${id}-file`} ref={file} type="file" accept=".csv,.txt,text/plain,text/csv" disabled={busy} />
        <p className="text-xs text-muted-foreground">
          支持邮箱----密码----客户端ID----刷新令牌，或带 email/password/client_id/refresh_token 表头的
          CSV。其它邮箱可使用应用密码收信或手工填码。
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={() => void importFile()}>
            导入邮箱池
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const blob = await exportEmailBindingStatus(accessToken);
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = "mailbox-binding-status.csv";
                anchor.click();
                URL.revokeObjectURL(url);
              })
            }
          >
            下载绑定状态 CSV
          </Button>
        </div>
        {notice && (
          <p role="status" className="text-sm">
            {notice}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          不修改你电脑里的原文件。系统保存已绑定记录，重复上传不能重置占用状态。
        </p>
      </section>
      {query.isError && <p role="alert">无法加载邮箱池，请检查连接后重试</p>}
      <section className="space-y-2">
        <Label htmlFor={`${id}-target`}>目标邮箱（可用 {available.length} 个）</Label>
        <select
          id={`${id}-target`}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          value={target}
          disabled={busy || Boolean(pending)}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="">按需分配一个未使用邮箱</option>
          {available.map((m) => (
            <option key={m.id} value={m.id}>
              {m.email}
            </option>
          ))}
        </select>
        <Button
          type="button"
          disabled={busy || Boolean(pending) || !available.length || environment.status !== "ready"}
          onClick={() =>
            void run(async () => {
              await proposeEmailBinding(accessToken, createAccountPoolJobId(), environment.id, target || undefined);
            })
          }
        >
          准备换绑，先核对资料
        </Button>
      </section>
      {job && (
        <BindingProgress
          key={job.id}
          accessToken={accessToken}
          job={job}
          busy={busy}
          run={run}
          setScreen={setScreen}
          mailboxStatus={query.data?.mailboxes.find((m) => m.id === job.mailbox_id)?.status}
        />
      )}
      <details>
        <summary>邮箱池绑定标记</summary>
        <ul className="max-h-48 overflow-auto text-sm">
          {query.data?.mailboxes.map((m) => (
            <li key={m.id}>
              {m.email} · {mailboxLabel(m.status)}
            </li>
          ))}
        </ul>
      </details>
      <Button type="button" variant="ghost" onClick={() => setScripts(!scripts)}>
        换绑脚本设置
      </Button>
      {scripts && <Scripts accessToken={accessToken} kind="rebinding" />}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Dialog open={screen !== null} onOpenChange={(value) => !value && setScreen(null)}>
        <DialogContent className="h-[85vh] w-[95vw] max-w-6xl">
          <DialogHeader>
            <DialogTitle>邮箱换绑浏览器</DialogTitle>
            <DialogDescription>查看不启用输入；接管后只在官网完成验证，不保留 Cookie</DialogDescription>
          </DialogHeader>
          {screen && (
            <iframe
              title="邮箱换绑浏览器画面"
              src={getAccountPoolOAuthBrowserUrl(screen)}
              className="h-full w-full border-0"
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function BindingProgress({
  accessToken,
  job,
  busy,
  run,
  setScreen,
  mailboxStatus,
}: {
  accessToken: string;
  job: BindingView;
  busy: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
  setScreen: (id: string) => void;
  mailboxStatus?: string;
}) {
  const id = useId();
  const code = useRef<HTMLInputElement>(null);
  const [ack, setAck] = useState(false);
  const authorized = useQuery({
    queryKey: accountPoolQueryKeys.automation(accessToken),
    queryFn: () => getAutomation(accessToken),
    enabled: Boolean(job?.oauth_job_id),
    refetchInterval:
      job?.oauth_job_id && !["completed", "cancelled", "failed", "uncertain"].includes(job.stage) ? 2000 : false,
  });
  const oauthJob = authorized.data?.jobs.find((j) => j.id === job?.oauth_job_id);
  const submitCode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = code.current?.value ?? "";
    if (code.current) code.current.value = "";
    if (job) void run(() => provideEmailBindingCode(accessToken, job.id, value));
  };
  return (
    <section className="space-y-3 rounded-lg border p-3" aria-label="换绑任务进度">
      <p className="break-all text-sm">
        原邮箱：{job.old_email}
        <br />
        目标邮箱：{job.target_email}
      </p>
      <p role="status" className="text-sm">
        {job.message}
      </p>
      <p className="text-xs text-muted-foreground">
        目标邮箱状态：{mailboxStatus === "bound" ? "已绑定" : "已预占"}。已提交的换绑不重放，失败不删除原卡。
      </p>
      {job.stage === "awaiting_confirmation" && (
        <>
          <p className="text-sm">
            确认后会暂停此卡的新请求，等待在途请求结束。换邮箱可能退出登录，随后必须重新授权；原邮箱被要求验证但无法收信时会暂停。
          </p>
          <div className="flex items-center gap-2">
            <Checkbox id={`${id}-ack`} checked={ack} onCheckedChange={(v) => setAck(v === true)} />
            <Label htmlFor={`${id}-ack`}>
              我确认将此卡从 {job.old_email} 换绑为 {job.target_email}
            </Label>
          </div>
          <Button
            type="button"
            disabled={busy || !ack}
            onClick={() => void run(() => confirmEmailBinding(accessToken, job))}
          >
            确认并开始换绑
          </Button>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        {!job.irreversible_started && !["completed", "cancelled"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "cancel"))}
          >
            取消，释放未使用邮箱
          </Button>
        )}
        {["failed", "verification_failed", "waiting_code"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "retry"))}
          >
            {retryLabel(job.email_changed, job.stage)}
          </Button>
        )}
        {job.irreversible_started && !job.email_changed && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "reconcile"))}
          >
            仅核实结果，不再次换绑
          </Button>
        )}
        {job.irreversible_started && ["uncertain", "failed", "verification_failed"].includes(job.stage) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "abort"))}
          >
            强制解除卡片占用
          </Button>
        )}
        {job.browser_session_id && !job.email_changed && (
          <>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setScreen((await openEmailBindingScreen(accessToken, job.id)).id);
                })
              }
            >
              查看浏览器
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "takeover"))}
            >
              停止脚本并接管
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void run(() => actOnEmailBinding(accessToken, job.id, "resume"))}
            >
              交回自动化
            </Button>
          </>
        )}
      </div>
      {job.stage === "waiting_code" && (
        <form className="flex items-end gap-2" onSubmit={submitCode}>
          <div>
            <Label htmlFor={`${id}-code`}>目标邮箱验证码</Label>
            <Input id={`${id}-code`} ref={code} autoComplete="off" pattern="[0-9]{6}" maxLength={6} required />
          </div>
          <Button type="submit" disabled={busy}>
            提交验证码
          </Button>
        </form>
      )}
      {oauthJob && <Authorization accessToken={accessToken} reauthorizingJob={oauthJob} />}
    </section>
  );
}
