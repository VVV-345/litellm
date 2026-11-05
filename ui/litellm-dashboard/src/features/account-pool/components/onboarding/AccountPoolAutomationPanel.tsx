/** 授权自动化工作区，登录资料仅保留在提交表单，画面访问复用临时浏览器票据。 */
"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fingerprint, ListChecks, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";
import {
  actOnAutomation,
  checkAutomationImage,
  createAutomationProfile,
  deleteAutomationProfile,
  getAutomation,
  openAutomationScreen,
  saveAutomationSettings,
  submitAutomation,
  updateAutomationProfile,
  type AutomationJob,
  type AutomationProfile,
  type AutomationSettings,
} from "../../api/AccountPoolAutomationApi";
import { createAccountPoolJobId, getAccountPoolOAuthBrowserUrl } from "../../api/AccountPoolManagementApi";
import { accountPoolEnvironmentOptions } from "../../hooks/accountPoolOptions";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { AccountPoolWorkspace } from "../shared/AccountPoolWorkspace";

const active = new Set(["starting", "running", "waiting", "manual", "callback_pending", "validating", "verifying"]);
const labels: Record<AutomationJob["stage"], string> = {
  queued: "排队",
  starting: "启动浏览器",
  running: "自动登录 / 2FA",
  waiting: "需要人工处理",
  manual: "人工接管",
  callback_pending: "等待回调",
  validating: "确认凭据",
  verifying: "模型验收",
  verification_failed: "验收未通过",
  completed: "验收通过",
  cancelled: "已取消",
  failed: "失败",
  expired: "已超时",
};
type Operation = (action: () => Promise<unknown>) => Promise<void>;
const systemLabels = { linux: "Linux", windows: "Windows", macos: "macOS" };

function SettingsForm({
  settings,
  disabled,
  save,
}: {
  settings: AutomationSettings;
  disabled: boolean;
  save: (settings: AutomationSettings) => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(settings.enabled ?? false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void save({
      enabled,
      total_timeout_seconds: Number(form.get("total")),
      human_timeout_seconds: Number(form.get("human")),
    });
  };
  return (
    <form className="grid max-w-xl gap-4" onSubmit={submit}>
      <p className="text-sm text-muted-foreground">
        默认关闭。先自检固定镜像，再开启。全局同时只运行一个账号，人工等待计入总时限
      </p>
      <Label>
        <Checkbox checked={enabled} onCheckedChange={(value) => setEnabled(value === true)} disabled={disabled} />
        开启授权自动化
      </Label>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="automation-total">总时限（秒）</Label>
          <Input
            id="automation-total"
            name="total"
            type="number"
            min={60}
            max={600}
            defaultValue={settings.total_timeout_seconds ?? 600}
            required
            disabled={disabled}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="automation-human">人工处理时限（秒）</Label>
          <Input
            id="automation-human"
            name="human"
            type="number"
            min={30}
            max={300}
            defaultValue={settings.human_timeout_seconds ?? 300}
            required
            disabled={disabled}
          />
        </div>
      </div>
      <Button className="w-fit" type="submit" disabled={disabled}>
        保存运行设置
      </Button>
    </form>
  );
}

function ProfileRow({
  profile,
  disabled,
  run,
  accessToken,
}: {
  profile: AutomationProfile;
  disabled: boolean;
  run: Operation;
  accessToken: string;
}) {
  const [name, setName] = useState(profile.name);
  const [enabled, setEnabled] = useState(profile.enabled ?? true);
  const locationHint = profile.auto_match_proxy ? "首次绑定时按代理出口匹配地区、语言和时区" : "保留原有手动配置";
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = {
      version: profile.version,
      name,
      enabled,
      locale: profile.locale,
      auto_match_proxy: profile.auto_match_proxy ?? false,
      os: profile.os ?? "linux",
    };
    void run(() => updateAutomationProfile(accessToken, profile.id, body));
  };
  return (
    <form className="flex flex-wrap items-center gap-3 rounded-lg border bg-background p-3" onSubmit={submit}>
      <Input
        aria-label={`指纹名称 ${profile.name}`}
        value={name}
        onChange={(event) => setName(event.target.value)}
        maxLength={80}
        required
        className="w-44"
        disabled={disabled}
      />
      <span className="text-sm text-muted-foreground">
        {systemLabels[profile.os ?? "linux"]} / {profile.matched_locale || profile.locale} /{" "}
        {profile.digest.slice(0, 10)}
      </span>
      <span className="text-xs text-muted-foreground">
        {profile.location
          ? `${profile.location.country} / ${profile.location.region || profile.location.city || profile.location.country_code} / ${profile.location.timezone}`
          : locationHint}
      </span>
      <span className="text-sm">
        {profile.bound_environment_id ? `已绑定 ${profile.bound_environment_id.slice(0, 8)}` : "未绑定，按顺序分配"}
      </span>
      <Label>
        <Checkbox checked={enabled} onCheckedChange={(value) => setEnabled(value === true)} disabled={disabled} />
        启用
      </Label>
      <Button type="submit" size="sm" variant="outline" disabled={disabled}>
        保存
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={disabled || Boolean(profile.bound_environment_id)}
        onClick={() => void run(() => deleteAutomationProfile(accessToken, profile.id))}
      >
        删除未绑定指纹
      </Button>
    </form>
  );
}

function JobRow({
  job,
  now,
  disabled,
  run,
  accessToken,
  view,
}: {
  job: AutomationJob;
  now: number;
  disabled: boolean;
  run: Operation;
  accessToken: string;
  view: (id: string) => Promise<void>;
}) {
  const [retryModel, setRetryModel] = useState(job.model || "");
  const deadline = job.human_expires_at || job.expires_at;
  const remaining = deadline ? Math.max(0, Math.ceil((Date.parse(deadline) - now) / 1000)) : null;
  const interactive = ["running", "waiting", "manual"].includes(job.stage);
  return (
    <article className="space-y-3 rounded-lg border bg-background p-3" aria-label={`任务 ${job.id.slice(0, 8)}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-3 text-sm">
          <strong>{labels[job.stage]}</strong>
          <span>卡片 {job.environment_id.slice(0, 8)}</span>
          <span>出口 {job.proxy_profile_id}</span>
          {active.has(job.stage) && remaining !== null && <span className="tabular-nums">剩余 {remaining} 秒</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          {interactive && (
            <Button size="sm" variant="outline" disabled={disabled} onClick={() => void run(() => view(job.id))}>
              查看画面
            </Button>
          )}
          {interactive && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() =>
                void run(() => actOnAutomation(accessToken, job.id, job.stage === "manual" ? "resume" : "takeover"))
              }
            >
              {job.stage === "manual" ? "交回自动化" : "停止脚本并接管"}
            </Button>
          )}
          {(active.has(job.stage) || job.stage === "queued") && (
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => void run(() => actOnAutomation(accessToken, job.id, "cancel"))}
            >
              取消
            </Button>
          )}
          {["failed", "expired", "cancelled", "verification_failed"].includes(job.stage) && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => void run(() => actOnAutomation(accessToken, job.id, "retry", retryModel))}
            >
              {job.credentials_saved || job.callback_consumed ? "仅重试验收" : "新会话重试"}
            </Button>
          )}
        </div>
      </div>
      <p className="text-sm text-muted-foreground" role="status">
        {job.message}
      </p>
      {job.stage === "verification_failed" && job.credentials_saved && (
        <Input
          aria-label="重试验收模型"
          value={retryModel}
          onChange={(event) => setRetryModel(event.target.value)}
          maxLength={256}
          className="max-w-xs"
          disabled={disabled}
          placeholder="选择这张卡片已启用的模型"
        />
      )}
      <p className="text-xs text-muted-foreground">
        凭据{job.credentials_saved ? "已保存" : "未确认"}，Chat {job.chat_verified ? "通过" : "未通过"}，Responses{" "}
        {job.responses_verified ? "通过" : "未通过"}
        {job.model ? `，模型 ${job.model}` : ""}
      </p>
    </article>
  );
}

export function AccountPoolAutomationPanel({
  accessToken,
  initialEnvironmentId,
}: {
  accessToken: string;
  initialEnvironmentId?: string;
}) {
  const client = useQueryClient();
  const queryOptions = {
    queryKey: accountPoolQueryKeys.automation(accessToken),
    queryFn: () => getAutomation(accessToken),
    retry: false as const,
    refetchInterval: 2000,
  };
  const query = useQuery(queryOptions);
  const cards = useQuery(accountPoolEnvironmentOptions(accessToken));
  const [busy, setBusy] = useState(false);
  const [credentials, setCredentials] = useState("");
  const [fileError, setFileError] = useState("");
  const [selectedCard, setSelectedCard] = useState(initialEnvironmentId ?? "");
  const [screen, setScreen] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const fileGeneration = useRef(0);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const run: Operation = async (action) => {
    setBusy(true);
    try {
      await action();
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.automation(accessToken) });
      toast.success("操作已完成");
    } catch {
      toast.error("操作未完成，请刷新任务或检查镜像自检、卡片与代理状态");
    } finally {
      setBusy(false);
    }
  };
  const submitJob = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = {
      id: createAccountPoolJobId(),
      environment_id: String(form.get("card")),
      credentials,
      model: String(form.get("model") || "") || null,
    };
    fileGeneration.current += 1;
    setCredentials("");
    void run(() => submitAutomation(accessToken, body));
  };
  const readCredentialFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    fileGeneration.current += 1;
    const generation = fileGeneration.current;
    setCredentials("");
    setFileError("");
    if (!file) return;
    if (file.size > 8192) {
      setFileError("文件不能超过 8 KB，只接收一个授权账号");
      return;
    }
    try {
      const text = await file.text();
      if (generation === fileGeneration.current) setCredentials(text);
    } catch {
      if (generation === fileGeneration.current) setFileError("无法读取文件，请重新选择");
    }
  };
  const submitProfile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const locale = form.get("locale");
    const os = form.get("os");
    const body: Parameters<typeof createAutomationProfile>[1] = {
      name: String(form.get("name")),
      locale: locale === "en-GB" || locale === "zh-CN" ? locale : "en-US",
      enabled: true,
      auto_match_proxy: locale === "auto",
      os: os === "windows" || os === "macos" ? os : "linux",
    };
    void run(() => createAutomationProfile(accessToken, body));
  };
  if (query.isLoading) return <p role="status">正在读取授权自动化状态</p>;
  if (!query.data) return <p role="alert">授权自动化不可用，请检查 Manager 连接后刷新</p>;
  const state = query.data;
  const emptyCards = (cards.data ?? []).filter((card) => {
    const oauthCard = card.supplier === "openai_codex" && card.authorization_flow === "browser_oauth";
    const configured = card.enabled && card.proxy_profile_id && !card.configuration_pending;
    return oauthCard && configured && ["awaiting_authorization", "error"].includes(card.status);
  });
  const selectedCardAvailable = emptyCards.some((card) => card.id === selectedCard);
  const linkedCardUnavailable =
    initialEnvironmentId && !cards.isPending && !emptyCards.some((card) => card.id === initialEnvironmentId);
  const jobs = (
    <div className="space-y-4">
      <form className="grid gap-3 rounded-lg border bg-muted/10 p-4 md:grid-cols-2" onSubmit={submitJob}>
        <div className="space-y-2">
          <Label htmlFor="automation-card">空白 Codex 卡片</Label>
          <select
            id="automation-card"
            name="card"
            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
            required
            disabled={busy}
            value={selectedCardAvailable ? selectedCard : ""}
            onChange={(event) => setSelectedCard(event.target.value)}
          >
            <option value="">选择已同步出口代理的空白卡片</option>
            {emptyCards.map((card) => (
              <option key={card.id} value={card.id}>
                {card.name}
              </option>
            ))}
          </select>
        </div>
        {linkedCardUnavailable && (
          <p role="status" className="text-sm text-muted-foreground md:col-span-2">
            所选卡片不满足空白 Codex 卡片与代理同步条件，请检查卡片状态后再选择。不会自动替换为其他账号
          </p>
        )}
        <div className="space-y-2">
          <Label htmlFor="automation-model">验收模型（可留空，授权后发现）</Label>
          <Input id="automation-model" name="model" maxLength={256} disabled={busy} />
        </div>
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="automation-file">登录资料文件</Label>
          <Input
            id="automation-file"
            type="file"
            accept=".txt,text/plain"
            disabled={busy}
            onChange={readCredentialFile}
          />
          <Label htmlFor="automation-credentials">单账号资料</Label>
          <Textarea
            id="automation-credentials"
            value={credentials}
            onChange={(event) => {
              fileGeneration.current += 1;
              setCredentials(event.target.value);
            }}
            autoComplete="off"
            spellCheck={false}
            maxLength={8192}
            placeholder="email----password----TOTPBase32"
            disabled={busy}
            required
          />
          <p className="text-xs text-muted-foreground">
            仅用于你有权登录的账号。提交后立即清空表单并加密保存；不覆盖已有认证，验证码或未知页面转人工处理
          </p>
          {fileError && (
            <p role="alert" className="text-sm text-destructive">
              {fileError}
            </p>
          )}
        </div>
        <Button className="w-fit" disabled={busy || !credentials || !selectedCardAvailable} type="submit">
          添加到串行队列
        </Button>
        {!state.settings.enabled && <p className="text-sm text-muted-foreground">当前自动化关闭，添加后不会开始登录</p>}
      </form>
      {state.jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有授权任务。先生成指纹，再添加空白卡片和授权资料</p>
      ) : (
        [...state.jobs].reverse().map((job) => (
          <JobRow
            key={job.id}
            job={job}
            now={now}
            disabled={busy}
            run={run}
            accessToken={accessToken}
            view={async (id) => {
              const session = await openAutomationScreen(accessToken, id);
              setScreen(session.id);
            }}
          />
        ))
      )}
    </div>
  );
  const profiles = (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        选择浏览器呈现的系统，容器仍运行 Linux，不继承
        Cookie。首次绑定按代理出口匹配地区、语言和时区，之后保持不变，已有账号不覆盖
      </p>
      <form className="flex flex-wrap items-end gap-3" onSubmit={submitProfile}>
        <div className="space-y-2">
          <Label htmlFor="automation-profile-name">配置名称</Label>
          <Input id="automation-profile-name" name="name" maxLength={80} required disabled={busy} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="automation-os">浏览器系统</Label>
          <select
            id="automation-os"
            name="os"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            disabled={busy}
            defaultValue="linux"
          >
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
            <option value="macos">macOS</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="automation-locale">浏览器语言</Label>
          <select
            id="automation-locale"
            name="locale"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            disabled={busy}
            defaultValue="auto"
          >
            <option value="auto">按代理出口自动匹配</option>
            <option>en-US</option>
            <option>en-GB</option>
            <option>zh-CN</option>
          </select>
        </div>
        <Button type="submit" disabled={busy || !state.image_checked}>
          生成并验证
        </Button>
      </form>
      {!state.image_checked && <p className="text-sm text-muted-foreground">请先在运行设置中完成镜像自检</p>}
      {state.profiles.map((profile) => (
        <ProfileRow
          key={`${profile.id}:${profile.version}`}
          profile={profile}
          disabled={busy || state.jobs.some((job) => job.profile_id === profile.id && active.has(job.stage))}
          run={run}
          accessToken={accessToken}
        />
      ))}
    </div>
  );
  return (
    <>
      <AccountPoolWorkspace
        title="授权自动化"
        defaultOpen
        items={[
          {
            value: "jobs",
            label: "任务队列",
            icon: ListChecks,
            summary: `${state.jobs.filter((job) => active.has(job.stage) || job.stage === "queued").length} 个待完成`,
            content: jobs,
          },
          {
            value: "profiles",
            label: "指纹配置",
            icon: Fingerprint,
            summary: `${state.profiles.length} 个配置`,
            content: profiles,
          },
          {
            value: "settings",
            label: "运行设置",
            icon: Settings2,
            summary: state.settings.enabled ? "已开启" : "已关闭",
            content: (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void run(() => checkAutomationImage(accessToken))}
                  >
                    自检浏览器镜像
                  </Button>
                  <span className="text-sm">{state.image_checked ? "当前镜像自检通过" : "当前镜像未通过自检"}</span>
                </div>
                <SettingsForm
                  key={JSON.stringify(state.settings)}
                  settings={state.settings}
                  disabled={busy}
                  save={(settings) => run(() => saveAutomationSettings(accessToken, state.version, settings))}
                />
              </div>
            ),
          },
        ]}
      />
      <Dialog open={screen !== null} onOpenChange={(open) => !open && setScreen(null)}>
        <DialogContent className="h-[85vh] w-[95vw] max-w-6xl">
          <DialogHeader>
            <DialogTitle>临时授权浏览器</DialogTitle>
            <DialogDescription>
              默认只能查看。回到任务队列点击“停止脚本并接管”，确认成功后才能输入；不支持录屏或保留 Cookie
            </DialogDescription>
          </DialogHeader>
          {screen && (
            <iframe
              title="授权浏览器画面"
              src={getAccountPoolOAuthBrowserUrl(screen)}
              className="h-full min-h-0 w-full border"
              referrerPolicy="no-referrer"
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
