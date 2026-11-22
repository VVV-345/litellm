/** 授权自动化指纹行组件，负责单个指纹配置的保存、释放、忘记与删除确认。 */
"use client";

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

const profileActionCopy = (action: ProfileDialogAction | null, profileName: string) => {
  if (action === "forget") {
    return {
      title: "确认忘记登录资料",
      description: "清除这份指纹和相关任务中的加密登录资料，不删除卡片、当前 OAuth 凭据或任务历史。",
      confirmLabel: "确认忘记",
    };
  }
  if (action === "release") {
    return {
      title: "确认释放预留",
      description: "仅在旧任务从未提交登录且已停止后释放；原卡保留，可在原卡重新填写资料。",
      confirmLabel: "确认释放",
    };
  }
  return {
    title: "确认删除指纹",
    description: `将删除“${profileName}”及其账号绑定。不会删除卡片凭据或历史任务；若要沿用此指纹，请在新任务选择迁移。`,
    confirmLabel: "确认删除",
  };
};

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
  const [name, setName] = useState(profile.name);
  const [enabled, setEnabled] = useState(profile.enabled ?? true);
  const [profileAction, setProfileAction] = useState<ProfileDialogAction | null>(null);
  const dialogCopy = profileActionCopy(profileAction, profile.name);
  const confirmProfileAction = () => {
    const action = profileAction;
    setProfileAction(null);
    if (action) void run(() => executeProfileAction(action, accessToken, profile.id));
  };
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
            <DialogTitle>{dialogCopy.title}</DialogTitle>
            <DialogDescription>{dialogCopy.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setProfileAction(null)}>
              取消
            </Button>
            <Button type="button" variant="destructive" disabled={disabled} onClick={confirmProfileAction}>
              {dialogCopy.confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </form>
  );
}
