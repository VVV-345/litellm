/** 授权自动化工作区，登录资料仅保留在提交表单，画面访问复用临时浏览器票据。 */
"use client";

import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fingerprint, ListChecks, Mail, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import {
  actOnAutomation,
  checkAutomationImage,
  createAutomationProfile,
  deleteAutomationJob,
  getAutomation,
  openAutomationScreen,
  saveAutomationSettings,
  safeAutomationOperationMessage,
  submitAutomation,
  type AutomationJob,
  type AutomationProfile,
} from "../../api/AccountPoolAutomationApi";
import { parseMailboxAccounts } from "../../api/AccountPoolOnboardingApi";
import { useAccountPoolProxyOptions } from "../../hooks/useAccountPoolProxyOptions";
import { ProfileRow, type AutomationOperation } from "./AccountPoolAutomationProfileRow";
import { AccountPoolOnboardingPanel } from "./AccountPoolOnboardingPanel";
import { AccountPoolLoginScriptSettings, SettingsForm } from "./AccountPoolLoginScriptSettings";
import { createAccountPoolJobId, getAccountPoolOAuthBrowserUrl } from "../../api/AccountPoolManagementApi";
import { accountPoolEnvironmentOptions } from "../../hooks/accountPoolOptions";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import { AccountPoolWorkspace } from "../shared/AccountPoolWorkspace";

const active = new Set(["starting", "running", "waiting", "manual", "callback_pending", "validating", "verifying"]);
type Operation = AutomationOperation;
const screenUnavailableAfterTakeover = new Error("screen-unavailable-after-takeover");

const retryActionKey = (job: AutomationJob) => {
  if (job.credentials_saved) return "retryVerify";
  return job.callback_consumed ? "retryCredential" : "retrySession";
};

const toggleManualControl = async (
  accessToken: string,
  job: AutomationJob,
  view: (id: string) => Promise<void>,
) => {
  const action = job.stage === "manual" ? "resume" : "takeover";
  const ack = await actOnAutomation(accessToken, job.id, action);
  if (action === "takeover" && ack.stage === "manual") {
    try {
      await view(job.id);
    } catch {
      throw screenUnavailableAfterTakeover;
    }
  }
};

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
  const { t } = useTranslation(undefined, { keyPrefix: "accountPool.automation" });
  const [retryModel, setRetryModel] = useState(job.credentials_saved ? job.model || "" : "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const finished = !active.has(job.stage) && job.stage !== "queued";
  const remaining = remainingJobSeconds(job, now);
  const interactive = ["running", "waiting", "manual"].includes(job.stage);
  const locale = profile?.auto_match_proxy ? profile.matched_locale : profile?.locale;
  const unverified = job.credentials_saved ? t("notConfirmed") : t("notVerified");
  return (
    <article className="space-y-3 rounded-lg border bg-background p-3" aria-label={t("jobName", { id: job.id.slice(0, 8) })}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-3 text-sm">
          <strong>{t(`status.${job.stage}`)}</strong>
          <span>{job.draft_pending ? t("draftCard") : t("card")} {job.environment_id.slice(0, 8)}</span>
          <span>{t("egress", { profile: job.proxy_profile_id })}</span>
          {locale && <span>{t("profileLanguage", { locale })}</span>}
          {active.has(job.stage) && remaining !== null && <span className="tabular-nums">{t("remaining", { seconds: remaining })}</span>}
        </div>
        <fieldset className="flex flex-wrap gap-2" disabled={disabled}>
          {interactive && (
            <Button size="sm" variant="outline" disabled={disabled} onClick={() => void run(() => view(job.id))}>
              {t("viewScreen")}
            </Button>
          )}
          {interactive && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => void run(() => toggleManualControl(accessToken, job, view))}
            >
              {job.stage === "manual" ? t("resume") : t("takeover")}
            </Button>
          )}
          {(active.has(job.stage) || job.stage === "queued") && (
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => void run(() => actOnAutomation(accessToken, job.id, "cancel"))}
            >
              {t("cancel")}
            </Button>
          )}
          {finished && job.cleanup_pending && (
            <Button size="sm" variant="outline" onClick={() => void run(() => actOnAutomation(accessToken, job.id, "cleanup"))}>
              {t("retryCleanup")}
            </Button>
          )}
          {!job.cleanup_pending && ["failed", "expired", "cancelled", "verification_failed"].includes(job.stage) && (
            <Button
              size="sm"
              variant="outline"
              disabled={cardMissing}
              onClick={() => void run(() => actOnAutomation(accessToken, job.id, "retry", retryModel.trim()))}
            >
              {t(retryActionKey(job))}
            </Button>
          )}
          {finished && !job.cleanup_pending && (
            <Button size="sm" variant="outline" onClick={() => setConfirmDelete(true)}>
              {t("deleteRecord")}
            </Button>
          )}
        </fieldset>
      </div>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("deleteTitle")}</DialogTitle>
            <DialogDescription>{t("deleteDescription")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmDelete(false)}>
              {t("cancel")}
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
              {t("confirmDelete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <p hidden={!cardMissing} className="text-sm text-muted-foreground">
        {t("missingCard")}
      </p>
      <p className="text-sm text-muted-foreground" role="status">
        {job.message}
      </p>
      {job.stage === "verification_failed" && (job.credentials_saved || job.callback_consumed) && (
        <Input
          aria-label={t("retryModel")}
          value={retryModel}
          onChange={(event) => setRetryModel(event.target.value)}
          maxLength={256}
          className="max-w-xs"
          disabled={disabled}
          placeholder={t("selectEnabled")}
        />
      )}
      <p className="text-xs text-muted-foreground">
        {t("verificationSummary", {
          credentials: job.credentials_saved ? t("saved") : t("unconfirmed"),
          chat: job.chat_verified ? t("passed") : unverified,
          responses: job.responses_verified ? t("passed") : unverified,
        })}
        {job.model ? t("modelSuffix", { model: job.model }) : ""}
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
  const { t, i18n } = useTranslation(undefined, { keyPrefix: "accountPool.automation" });
  const client = useQueryClient();
  const localeNames = new Intl.DisplayNames(i18n.language, { type: "language" });
  const queryOptions = {
    queryKey: accountPoolQueryKeys.automation(accessToken),
    queryFn: () => getAutomation(accessToken),
    retry: false as const,
    refetchInterval: (q: { state: { data?: { jobs: AutomationJob[] } } }) => {
      if (q.state.data) {
        return q.state.data.jobs.some((job) => active.has(job.stage) || job.stage === "queued") ? 2000 : false;
      }
      return reauthorizingJob ? 2000 : false;
    },
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
  const [screen, setScreen] = useState<{ sessionId: string; jobId: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const fileGeneration = useRef(0);
  const hasActiveJobs =
    (query.data?.jobs.some((job) => active.has(job.stage)) ?? false) ||
    (reauthorizingJob ? active.has(reauthorizingJob.stage) : false);
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasActiveJobs]);
  const run: Operation = async (action) => {
    setBusy(true);
    setOperationError(null);
    try {
      await action();
      await client.invalidateQueries({ queryKey: accountPoolQueryKeys.automation(accessToken) });
      toast.success(t("done"));
    } catch (error) {
      const message = error === screenUnavailableAfterTakeover
        ? t("screenUnavailable")
        : safeAutomationOperationMessage(error);
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
      setFileError(t("invalidInput"));
      return;
    }
    const name = explicitName || `Codex-套餐待识别-${account ?? ""}`.slice(0, 80);
    const draft = creatingCard ? { name, proxy_profile_id: newProxyId } : undefined;
    const key = JSON.stringify([environmentId, model, draft, reuseProfileId]);
    const id = pendingSubmission.current?.key === key ? pendingSubmission.current.id : createAccountPoolJobId();
    pendingSubmission.current = { key, id };
    const submittedCredentials = credentials;
    const generation = ++fileGeneration.current;
    setFileError("");
    void run(async () => {
      await submitAutomation(accessToken, {
        id, environment_id: draft ? id : environmentId, credentials: submittedCredentials, model,
        ...(draft ? { draft } : {}), ...(reuseProfileId ? { reuse_profile_id: reuseProfileId } : {}),
      });
      pendingSubmission.current = null;
      if (generation === fileGeneration.current) {
        setCredentials((current) => current === submittedCredentials ? "" : current);
      }
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
      setFileError(t("fileTooLarge"));
      return;
    }
    try {
      const text = await file.text();
      if (generation === fileGeneration.current) setCredentials(text);
    } catch {
      if (generation === fileGeneration.current) setFileError(t("fileReadError"));
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
  const openJobScreen = async (id: string) => {
    const session = await openAutomationScreen(accessToken, id);
    setScreen({ sessionId: session.id, jobId: id });
  };
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
      view={openJobScreen}
    />
  );
  const screenJob = screen
    ? query.data?.jobs.find((job) => job.id === screen.jobId) ??
      (reauthorizingJob?.id === screen.jobId ? reauthorizingJob : undefined)
    : undefined;
  const screenInteractive = Boolean(screenJob && ["running", "waiting", "manual"].includes(screenJob.stage));
  const screenRemaining = screenJob && active.has(screenJob.stage) ? remainingJobSeconds(screenJob, now) : null;
  const screenDialog = (
    <Dialog open={screen !== null} onOpenChange={(open) => !open && setScreen(null)}>
      <DialogContent className="h-[85vh] w-[95vw] max-w-6xl">
        <DialogHeader>
          <div className="flex flex-wrap items-center justify-between gap-2 pr-6">
            <DialogTitle>{t("screenTitle")}</DialogTitle>
            {screenJob && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded bg-muted px-2 py-0.5 font-medium">{t(`status.${screenJob.stage}`)}</span>
                {screenRemaining !== null && <span className="tabular-nums text-muted-foreground">{t("remaining", { seconds: screenRemaining })}</span>}
                {screenInteractive && (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void run(() => toggleManualControl(accessToken, screenJob, openJobScreen))}
                  >
                    {screenJob.stage === "manual" ? t("resume") : t("takeover")}
                  </Button>
                )}
              </div>
            )}
          </div>
          <DialogDescription>
            {t("screenHelp")}
          </DialogDescription>
        </DialogHeader>
        {screen && (
          <iframe
            title={t("screenFrame")}
            src={getAccountPoolOAuthBrowserUrl(screen.sessionId)}
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
        {query.isError && <p role="alert">{t("progressUnavailable")}</p>}
        {operationError && (
          <p role="alert" className="text-sm text-destructive">{operationError}</p>
        )}
        {currentJob || !query.isSuccess || !query.isFetchedAfterMount ? (
          renderJob(currentJob ?? reauthorizingJob)
        ) : (
          <p role="status">{t("jobNotListed")}</p>
        )}
        {screenDialog}
      </div>
    );
  }
  if (query.isLoading) return <p role="status">{t("loading")}</p>;
  if (!query.data) return <p role="alert">{t("unavailable")}</p>;
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
          <Label htmlFor="automation-card">{t("blankCard")}</Label>
          <select
            id="automation-card"
            name="card"
            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
            required
            disabled={busy}
            value={creatingCard || selectedCardAvailable ? selectedCard : ""}
            onChange={(event) => setSelectedCard(event.target.value)}
          >
            <option value="">{t("selectCard")}</option>
            <option value="__new__">{t("newCard")}</option>
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
              <Label htmlFor="automation-card-name">{t("cardName")}</Label>
              <Input
                id="automation-card-name"
                name="card_name"
                maxLength={80}
                disabled={busy}
                placeholder={t("nameHint")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="automation-proxy-profile">{t("proxy")}</Label>
              <select
                id="automation-proxy-profile"
                required
                disabled={busy || profilesQuery.isPending}
                value={newProxyId}
                onChange={(event) => setNewProxyId(event.target.value)}
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="">{t("selectProxy")}</option>
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
            {t("unavailableCard")}
          </p>
        )}
        <div className="space-y-2">
          <Label htmlFor="automation-model">{t("model")}</Label>
          <Input id="automation-model" name="model" maxLength={256} disabled={busy} />
        </div>
        {orphanedProfiles.length > 0 && (
          <div className="space-y-2">
            <Label htmlFor="automation-reuse-profile">{t("reuseProfile")}</Label>
            <select id="automation-reuse-profile" name="reuse_profile_id" disabled={busy}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm">
              <option value="">{t("availableProfile")}</option>
              {orphanedProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </div>
        )}
        <div className="space-y-2 md:col-span-2">
          <Label htmlFor="automation-file">{t("file")}</Label>
          <Input
            id="automation-file"
            type="file"
            accept=".txt,text/plain"
            disabled={busy}
            onChange={readCredentialFile}
          />
          <Label htmlFor="automation-credentials">{t("credentials")}</Label>
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
            {t("authorizedOnly")}
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
          {creatingCard ? t("createAndQueue") : t("enqueue")}
        </Button>
        {!state.settings.enabled && <p className="text-sm text-muted-foreground">{t("paused")}</p>}
      </form>
      {state.jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        [...state.jobs].reverse().map(renderJob)
      )}
    </div>
  );
  const profiles = (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t("profileHelp")}
      </p>
      <form className="flex flex-wrap items-end gap-3" onSubmit={submitProfile}>
        <div className="space-y-2">
          <Label htmlFor="automation-profile-name">{t("profileName")}</Label>
          <Input id="automation-profile-name" name="name" maxLength={80} required disabled={busy} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="automation-os">{t("os")}</Label>
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
          <Label htmlFor="automation-locale">{t("language")}</Label>
          <select
            id="automation-locale"
            name="locale"
            className="h-9 rounded-md border bg-background px-3 text-sm"
            disabled={busy}
            defaultValue="auto"
          >
            <option value="auto">{t("matchProxy")}</option>
            {(state.locale_presets ?? ["en-US", "en-GB", "zh-CN"]).map((locale) => (
              <option key={locale} value={locale}>
                {localeNames.of(locale)} ({locale})
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" disabled={busy || !state.image_checked}>
          {t("generate")}
        </Button>
      </form>
      {!state.image_checked && <p className="text-sm text-muted-foreground">{t("checkFirst")}</p>}
      <p className="text-sm text-muted-foreground">
        {t("transferHelp")}
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
        title={t("title")}
        defaultOpen
        items={[
          {
            value: "jobs",
            label: t("jobs"),
            icon: ListChecks,
            summary: t("pendingJobs", { count: state.jobs.filter((job) => active.has(job.stage) || job.stage === "queued").length }),
            content: jobs,
          },
          {
            value: "profiles",
            label: t("profiles"),
            icon: Fingerprint,
            summary: t("profileCount", { count: state.profiles.length }),
            content: profiles,
          },
          {
            value: "settings",
            label: t("settings"),
            icon: Settings2,
            summary: state.settings.enabled ? t("enabled") : t("disabled"),
            content: (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void run(() => checkAutomationImage(accessToken))}
                  >
                    {t("checkImage")}
                  </Button>
                  <span className="text-sm">{state.image_checked ? t("imagePassed") : t("imageUnchecked")}</span>
                </div>
                <SettingsForm
                  key={JSON.stringify(state.settings)}
                  settings={state.settings}
                  disabled={busy}
                  save={(settings) => run(() => saveAutomationSettings(accessToken, state.version, settings, state.settings))}
                />
                <AccountPoolLoginScriptSettings accessToken={accessToken} />
              </div>
            ),
          },
          {
            value: "legacy-oauth",
            label: t("manualOAuth"),
            icon: Mail,
            content: <AccountPoolOnboardingPanel accessToken={accessToken} legacyOAuth />,
          },
        ]}
      />
      {screenDialog}
    </>
  );
}
