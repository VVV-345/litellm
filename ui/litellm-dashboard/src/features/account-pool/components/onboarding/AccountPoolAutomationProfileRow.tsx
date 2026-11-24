/** 授权自动化指纹行组件，负责单个指纹配置的保存、释放、忘记与删除确认。 */
"use client";

import { useTranslation } from "react-i18next";
import { useState, type FormEvent } from "react";

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

import {
  deleteAutomationProfile,
  forgetAutomationLogin,
  releaseAutomationReservation,
  updateAutomationProfile,
  type AutomationProfile,
} from "../../api/AccountPoolAutomationApi";

export type AutomationOperation = (action: () => Promise<unknown>) => Promise<void>;

const systemLabels = { linux: "Linux", windows: "Windows", macos: "macOS" };

type ProfileDialogAction = "delete" | "forget" | "release";

const executeProfileAction = (action: ProfileDialogAction, accessToken: string, profileId: string) => {
  if (action === "forget") return forgetAutomationLogin(accessToken, profileId);
  if (action === "release") return releaseAutomationReservation(accessToken, profileId);
  return deleteAutomationProfile(accessToken, profileId);
};

export function ProfileRow({
  profile,
  orphaned,
  disabled,
  run,
  accessToken,
}: {
  profile: AutomationProfile;
  orphaned: boolean;
  disabled: boolean;
  run: AutomationOperation;
  accessToken: string;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "accountPool.automation" });
  const [name, setName] = useState(profile.name);
  const [enabled, setEnabled] = useState(profile.enabled ?? true);
  const [profileAction, setProfileAction] = useState<ProfileDialogAction | null>(null);
  const confirmProfileAction = () => {
    const action = profileAction;
    setProfileAction(null);
    if (action) void run(() => executeProfileAction(action, accessToken, profile.id));
  };
  const locationHint = profile.auto_match_proxy ? t("matchLocation") : t("keepManual");
  const bindingLabel = profile.bound_environment_id
    ? `${profile.reservation ? t("reserved") : t("bound")} ${profile.bound_environment_id.slice(0, 8)}`
    : t("unbound");
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
        aria-label={t("editProfileName", { name: profile.name })}
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
      <span className="text-sm">{orphaned ? t("orphan") : bindingLabel}</span>
      <Label>
        <Checkbox checked={enabled} onCheckedChange={(value) => setEnabled(value === true)} disabled={disabled} />
        {t("enableProfile")}
      </Label>
      <Button type="submit" size="sm" variant="outline" disabled={disabled}>
        {t("saveProfile")}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={disabled || (Boolean(profile.bound_environment_id) && !orphaned)}
        onClick={() => setProfileAction("delete")}
      >
        {orphaned ? t("deleteBinding") : t("deleteProfile")}
      </Button>
      {profile.reservation && (
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setProfileAction("release")}>
          {t("releaseReservation")}
        </Button>
      )}
      {profile.login_saved && (
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => setProfileAction("forget")}>
          {t("forgetCredentials")}
        </Button>
      )}
      <Dialog open={profileAction !== null} onOpenChange={(open) => !open && setProfileAction(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t(`profileDialog.${profileAction ?? "delete"}.title`, { name: profile.name })}</DialogTitle>
            <DialogDescription>{t(`profileDialog.${profileAction ?? "delete"}.description`, { name: profile.name })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setProfileAction(null)}>
              {t("cancel")}
            </Button>
            <Button type="button" variant="destructive" disabled={disabled} onClick={confirmProfileAction}>
              {t(`profileDialog.${profileAction ?? "delete"}.confirmLabel`, { name: profile.name })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </form>
  );
}
