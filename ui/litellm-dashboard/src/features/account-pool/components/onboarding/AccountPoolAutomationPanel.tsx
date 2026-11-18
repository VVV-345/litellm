/** 授权自动化工作区，登录资料仅保留在提交表单，画面访问复用临时浏览器票据。 */
"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fingerprint, ListChecks, Mail, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/lib/toast";
import { ApiError } from "@/lib/http/client";
import {
  actOnAutomation,
  checkAutomationImage,
  createAutomationProfile,
  deleteAutomationJob,
  deleteAutomationProfile,
  forgetAutomationLogin,
  getAutomation,
  openAutomationScreen,
  releaseAutomationReservation,
  saveAutomationSettings,
  submitAutomation,
  updateAutomationProfile,
  type AutomationJob,
  type AutomationProfile,
} from "../../api/AccountPoolAutomationApi";
import { parseMailboxAccounts } from "../../api/AccountPoolOnboardingApi";
import { useAccountPoolProxyOptions } from "../../hooks/useAccountPoolProxyOptions";
import { AccountPoolOnboardingPanel } from "./AccountPoolOnboardingPanel";
import { AccountPoolLoginScriptSettings, SettingsForm } from "./AccountPoolLoginScriptSettings";
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
const localeNames = new Intl.DisplayNames("zh-CN", { type: "language" });
const screenUnavailableAfterTakeover = new Error("已接管，但画面未能打开。请点「查看画面」重开，无需重新授权");

function ProfileRow({
  profile,
  orphaned,
  disabled,
  run,
  accessToken,
}: {
  profile: AutomationProfile;
  orphaned: boolean;
  disabled: boolean;
  run: Operation;
  accessToken: string;
}) {
  const [name, setName] = useState(profile.name);
  const [enabled, setEnabled] = useState(profile.enabled ?? true);
  const [profileAction, setProfileAction] = useState<"delete" | "forget" | "release" | null>(null);
  const locationHint = profile.auto_match_proxy ? "首次绑定时按代理出口匹配地区、语言和时区" : "保留原有手动配置";
  const bindingLabel = profile.bound_environment_id
    ? `${profile.reservation ? "预留" : "已绑定"} ${profile.bound_environment_id.slice(0, 8)}`
    : "未绑定，按顺序分配";
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
      <span className="text-sm">{orphaned ? "原卡片已删除，绑定已失效" : bindingLabel}</span>
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
        disabled={disabled || (Boolean(profile.bound_environment_id) && !orphaned)}
        onClick={() => setProfileAction("delete")}
      >
        {orphaned ? "删除失效绑定" : "删除指纹"}
      </Button>
      {profile.reservation && (
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setProfileAction("release")}>
          释放未登录预留
        </Button>
      )}
      {profile.login_saved && (
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setProfileAction("forget")}>
          忘记登录资料
        </Button>
      )}
      <Dialog open={profileAction !== null} onOpenChange={(open) => !open && setProfileAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {profileAction === "forget" ? "确认忘记登录资料" : profileAction === "release" ? "确认释放预留" : "确认删除指纹"}
            </DialogTitle>
            <DialogDescription>
              {profileAction === "forget"
                ? "清除这份指纹和相关任务中的加密登录资料，不删除卡片、当前 OAuth 凭据或任务历史。"
                : profileAction === "release"
                  ? "仅在旧任务从未提交登录且已停止后释放；原卡保留，可在原卡重新填写资料。"
                  : `将删除“${profile.name}”及其账号绑定。不会删除卡片凭据或历史任务；若要沿用此指纹，请在新任务选择迁移。`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setProfileAction(null)}>
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={disabled}
              onClick={() => {
                const action = profileAction;
                setProfileAction(null);
                void run(() => (
                  action === "forget" ? forgetAutomationLogin(accessToken, profile.id)
                    : action === "release" ? releaseAutomationReservation(accessToken, profile.id)
                      : deleteAutomationProfile(accessToken, profile.id)
                ));
              }}
            >
              {profileAction === "forget" ? "确认忘记" : profileAction === "release" ? "确认释放" : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </form>
  );
}

const remainingJobSeconds = (job: AutomationJob, now: number): number | null => {
  const deadline = job.human_expires_at || job.expires_at;
  return deadline ? Math.max(0, Math.ceil((Date.parse(deadline) - now) / 1000)) : null;
};

function JobRow({
  job,
  profile,
  cardMissing,
  now,
  disabled,
  run,
  accessToken,
  view,
}: {
  job: AutomationJob;
  profile?: AutomationProfile;
  cardMissing: boolean;
  now: number;
  disabled: boolean;
  run: Operation;
  accessToken: string;
  view: (id: string) => Promise<void>;
}) {
  const [retryModel, setRetryModel] = useState(job.credentials_saved ? job.model || "" : "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const finished = !active.has(job.stage) && job.stage !== "queued";
  const remaining = remainingJobSeconds(job, now);
  const interactive = ["running", "waiting", "manual"].includes(job.stage);
  const locale = profile?.auto_match_proxy ? profile.matched_locale : profile?.locale;
  const unverified = job.credentials_saved ? "未确认通过" : "未验收";
  return (
    <article className="space-y-3 rounded-lg border bg-background p-3" aria-label={`任务 ${job.id.slice(0, 8)}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-3 text-sm">
          <strong>{labels[job.stage]}</strong>
          <span>{job.draft_pending ? "待创建卡片" : "卡片"} {job.environment_id.slice(0, 8)}</span>
          <span>出口 {job.proxy_profile_id}</span>
          {locale && <span>配置语言 {locale}</span>}
          {active.has(job.stage) && remaining !== null && <span className="tabular-nums">剩余 {remaining} 秒</span>}
        </div>
        <fieldset className="flex flex-wrap gap-2" disabled={disabled}>
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
                void run(async () => {
                  const action = job.stage === "manual" ? "resume" : "takeover";
                  const ack = await actOnAutomation(accessToken, job.id, action);
                  if (action === "takeover" && ack.stage === "manual") {
                    try {
                      await view(job.id);
                    } catch {
                      throw screenUnavailableAfterTakeover;
                    }
                  }
                })
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
          {finished && job.cleanup_pending && (
            <Button size="sm" variant="outline" onClick={() => void run(() => actOnAutomation(accessToken, job.id, "cleanup"))}>
              重试清理
            </Button>
          )}
          {!job.cleanup_pending && ["failed", "expired", "cancelled", "verification_failed"].includes(job.stage) && (
            <Button
              size="sm"
              variant="outline"
              disabled={cardMissing}
              onClick={() => void run(() => actOnAutomation(accessToken, job.id, "retry", retryModel.trim()))}
            >
              {job.credentials_saved ? "仅重试验收" : job.callback_consumed ? "核对凭据后重试" : "新会话重试"}
            </Button>
          )}
          {finished && !job.cleanup_pending && (
            <Button size="sm" variant="outline" onClick={() => setConfirmDelete(true)}>
              删除记录
            </Button>
          )}
        </fieldset>
      </div>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除授权任务记录</DialogTitle>
            <DialogDescription>只删除任务历史。已保存的一键重授权资料请在指纹配置中单独忘记。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={disabled}
              onClick={() => {
                setConfirmDelete(false);
                void run(() => deleteAutomationJob(accessToken, job.id));
              }}
            >
              确认删除记录
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <p hidden={!cardMissing} className="text-sm text-muted-foreground">
        原卡片已删除，请使用新卡提交新任务；不能重试旧卡任务
      </p>
      <p className="text-sm text-muted-foreground" role="status">
        {job.message}
      </p>
      {job.stage === "verification_failed" && (job.credentials_saved || job.callback_consumed) && (
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
        凭据{job.credentials_saved ? "已保存" : "未确认"}，Chat {job.chat_verified ? "通过" : unverified}，Responses{" "}
        {job.responses_verified ? "通过" : unverified}
        {job.model ? `，模型 ${job.model}` : ""}
      </p>
    </article>
  );
}

export function AccountPoolAutomationPanel({
  accessToken,
  initialEnvironmentId,
  reauthorizingJob,
}: {
  accessToken: string;
  initialEnvironmentId?: string;
  reauthorizingJob?: AutomationJob;
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
  const [operationError, setOperationError] = useState<string | null>(null);
  const [credentials, setCredentials] = useState("");
  const [fileError, setFileError] = useState("");
  const [selectedCard, setSelectedCard] = useState(initialEnvironmentId ?? "");
  const creatingCard = selectedCard === "__new__";
  const [newProxyId, setNewProxyId] = useState("");
  const { options: proxyOptions, profilesQuery, defaultLabel } = useAccountPoolProxyOptions(accessToken, creatingCard);
  const pendingSubmission = useRef<{ key: string; id: string } | null>(null);
  const validProxy = profilesQuery.isSuccess && proxyOptions.some((option) => option.value === newProxyId);
  const [screen, setScreen] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const fileGeneration = useRef(0);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const run: Operation = async (action) => {
    setBusy(true);
    setOperationError(null);
    try {
      await action();
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.automation(accessToken) });
      toast.success("操作已完成");
    } catch (error) {
      const knownError = error === screenUnavailableAfterTakeover || (error instanceof ApiError && [404, 409].includes(error.status));
      const message = knownError && error instanceof Error
        ? error.message
        : "操作未完成，请刷新任务或检查镜像自检、卡片与代理状态";
      setOperationError(message);
      toast.error(message);
      if (error === screenUnavailableAfterTakeover) {
        await client.invalidateQueries({ queryKey: accountPoolQueryKeys.automation(accessToken) });
      }
    } finally {
      setBusy(false);
    }
  };
  const submitJob = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const environmentId = String(form.get("card"));
    const model = String(form.get("model") || "") || null;
    const reuseProfileId = String(form.get("reuse_profile_id") || "") || undefined;
    const explicitName = String(form.get("card_name") || "").trim();
    const account = creatingCard
      ? (() => {
          try {
            const entries = parseMailboxAccounts(credentials);
            return entries.length === 1 && entries[0].totp_secret ? entries[0].label.toLowerCase() : null;
          } catch {
            return null;
          }
        })()
      : null;
    if (creatingCard && (!validProxy || !account || new TextEncoder().encode(credentials).length > 8192)) {
      setFileError("请选有效代理，并填写一条有效的 2FA 登录资料");
      return;
    }
    const name = explicitName || `Codex-套餐待识别-${account ?? ""}`.slice(0, 80);
    const draft = creatingCard ? { name, proxy_profile_id: newProxyId } : undefined;
    const key = JSON.stringify([environmentId, model, draft, reuseProfileId]);
    const id = pendingSubmission.current?.key === key ? pendingSubmission.current.id : createAccountPoolJobId();
    pendingSubmission.current = { key, id };
    fileGeneration.current += 1;
    setCredentials("");
    setFileError("");
    void run(async () => {
      await submitAutomation(accessToken, {
        id, environment_id: draft ? id : environmentId, credentials, model,
        ...(draft ? { draft } : {}), ...(reuseProfileId ? { reuse_profile_id: reuseProfileId } : {}),
      });
      pendingSubmission.current = null;
    });
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
    if (typeof locale !== "string") return;
    const os = form.get("os");
    const body: Parameters<typeof createAutomationProfile>[1] = {
      name: String(form.get("name")),
      locale: locale === "auto" ? "en-US" : locale,
      enabled: true,
      auto_match_proxy: locale === "auto",
      os: os === "windows" || os === "macos" ? os : "linux",
    };
    void run(() => createAutomationProfile(accessToken, body));
  };
  const currentJob = query.data?.jobs.find((job) => job.id === reauthorizingJob?.id);
  const currentStage = currentJob?.stage;
  useEffect(() => {
    if (currentStage && currentStage !== "queued" && !active.has(currentStage)) {
      void client.invalidateQueries({ queryKey: accountPoolQueryKeys.environmentsRoot });
      void client.invalidateQueries({ queryKey: accountPoolQueryKeys.credentials(accessToken) });
    }
  }, [accessToken, client, currentStage]);
  const renderJob = (job: AutomationJob) => (
    <JobRow
      key={job.id}
      job={job}
      profile={query.data?.profiles.find((profile) => profile.id === job.profile_id)}
      cardMissing={!job.draft_pending && cards.isSuccess && !cards.data.some((card) => card.id === job.environment_id)}
      now={now}
      disabled={busy}
      run={run}
      accessToken={accessToken}
      view={async (id) => {
        const session = await openAutomationScreen(accessToken, id);
        setScreen(session.id);
      }}
    />
  );
  const screenDialog = (
    <Dialog open={screen !== null} onOpenChange={(open) => !open && setScreen(null)}>
      <DialogContent className="h-[85vh] w-[95vw] max-w-6xl">
        <DialogHeader>
          <DialogTitle>临时授权浏览器</DialogTitle>
          <DialogDescription>
            查看画面不会开启输入。接管成功后请在授权页完成验证；返回任务可交回自动化。不支持录屏或保留 Cookie
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
  );
  if (reauthorizingJob) {
    return (
      <div className="space-y-3" aria-live="polite">
        {query.isError && <p role="alert">无法读取最新任务进度，正在重试</p>}
        {operationError && (
          <p role="alert" className="text-sm text-destructive">{operationError}</p>
        )}
        {currentJob || !query.isSuccess || !query.isFetchedAfterMount ? (
          renderJob(currentJob ?? reauthorizingJob)
        ) : (
          <p role="status">当前任务不在此列表，请刷新或到授权任务队列查看</p>
        )}
        {screenDialog}
      </div>
    );
  }
  if (query.isLoading) return <p role="status">正在读取授权自动化状态</p>;
  if (!query.data) return <p role="alert">授权自动化不可用，请检查 Manager 连接后刷新</p>;
  const state = query.data;
  const orphanedProfiles = state.profiles.filter((profile) =>
    profile.bound_environment_id && cards.isSuccess &&
    !cards.data.some((card) => card.id === profile.bound_environment_id) &&
    !state.jobs.some((job) => job.environment_id === profile.bound_environment_id && job.draft_pending && job.stage !== "cancelled"),
  );
  const emptyCards = (cards.data ?? []).filter((card) => {
    const oauthCard = card.supplier === "openai_codex" && card.authorization_flow === "browser_oauth";
    const configured = card.enabled && card.proxy_profile_id && !card.configuration_pending;
    return oauthCard && configured && !card.auth_file_bound && ["awaiting_authorization", "error"].includes(card.status);
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
            value={creatingCard || selectedCardAvailable ? selectedCard : ""}
            onChange={(event) => setSelectedCard(event.target.value)}
          >
            <option value="">选择已同步出口代理的空白卡片</option>
            <option value="__new__">新建 Codex 卡片</option>
            {emptyCards.map((card) => (
              <option key={card.id} value={card.id}>
                {card.name}
              </option>
            ))}
          </select>
        </div>
        {creatingCard && (
          <>
            <div className="space-y-2">
              <Label htmlFor="automation-card-name">卡片名称（可留空）</Label>
              <Input
                id="automation-card-name"
                name="card_name"
                maxLength={80}
                disabled={busy}
                placeholder="留空按 Codex-套餐-账号 命名"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="automation-proxy-profile">OAuth 出站代理</Label>
              <select
                id="automation-proxy-profile"
                required
                disabled={busy || profilesQuery.isPending}
                value={newProxyId}
                onChange={(event) => setNewProxyId(event.target.value)}
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="">选择代理节点</option>
                {proxyOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{defaultLabel}</p>
            </div>
          </>
        )}
        {linkedCardUnavailable && !creatingCard && (
          <p role="status" className="text-sm text-muted-foreground md:col-span-2">
            所选卡片不满足空白 Codex 卡片与代理同步条件，请检查卡片状态后再选择。不会自动替换为其他账号
          </p>
        )}
        <div className="space-y-2">
          <Label htmlFor="automation-model">验收模型（可留空，授权后发现）</Label>
          <Input id="automation-model" name="model" maxLength={256} disabled={busy} />
        </div>
        {orphanedProfiles.length > 0 && (
          <div className="space-y-2">
            <Label htmlFor="automation-reuse-profile">沿用原指纹（只限同一账号）</Label>
            <select id="automation-reuse-profile" name="reuse_profile_id" disabled={busy}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm">
              <option value="">使用现有绑定或未绑定指纹</option>
              {orphanedProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </div>
        )}
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
            仅用于你有权登录的账号。验证账号后，登录资料加密保存供原卡重新授权，可在指纹配置中忘记；验证码或未知页面转人工处理。
          </p>
          {fileError && (
            <p role="alert" className="text-sm text-destructive">
              {fileError}
            </p>
          )}
        </div>
        <Button
          className="w-fit"
          disabled={busy || !credentials || !(creatingCard ? validProxy : selectedCardAvailable)}
          type="submit"
        >
          {creatingCard ? "创建卡片并加入队列" : "添加到串行队列"}
        </Button>
        {!state.settings.enabled && <p className="text-sm text-muted-foreground">当前自动化关闭，添加后不会开始登录</p>}
      </form>
      {state.jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有授权任务。先生成指纹，再添加空白卡片和授权资料</p>
      ) : (
        [...state.jobs].reverse().map(renderJob)
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
            {(state.locale_presets ?? ["en-US", "en-GB", "zh-CN"]).map((locale) => (
              <option key={locale} value={locale}>
                {localeNames.of(locale)} ({locale})
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" disabled={busy || !state.image_checked}>
          生成并验证
        </Button>
      </form>
      {!state.image_checked && <p className="text-sm text-muted-foreground">请先在运行设置中完成镜像自检</p>}
      <p className="text-sm text-muted-foreground">
        原卡删除且任务清理完成后，可在新任务中为同一账号选择沿用原指纹。不迁移旧 OAuth 凭据。
      </p>
      {state.profiles.map((profile) => (
        <ProfileRow
          key={`${profile.id}:${profile.version}`}
          profile={profile}
          orphaned={orphanedProfiles.some(({ id }) => id === profile.id)}
          disabled={
            busy ||
            state.jobs.some((job) => job.profile_id === profile.id &&
              (active.has(job.stage) || job.stage === "queued" || job.cleanup_pending))
          }
          run={run}
          accessToken={accessToken}
        />
      ))}
    </div>
  );
  return (
    <>
      {operationError && (
        <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          {operationError}
        </p>
      )}
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
                <AccountPoolLoginScriptSettings accessToken={accessToken} />
              </div>
            ),
          },
          {
            value: "legacy-oauth",
            label: "手动 OAuth / 邮箱库存",
            icon: Mail,
            content: <AccountPoolOnboardingPanel accessToken={accessToken} legacyOAuth />,
          },
        ]}
      />
      {screenDialog}
    </>
  );
}
