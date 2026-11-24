/** 用表单编辑和批量应用认证元数据，默认模板保存在服务端且不包含认证内容。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

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
import { toast } from "@/lib/toast";
import {
  getAccountPoolSettings,
  patchAccountPoolAuthFileFields,
  updateAccountPoolSettings,
} from "../../api/AccountPoolManagementApi";
import { accountPoolProxyProfileOptions } from "../../hooks/accountPoolOptions";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";
import {
  authFileTemplateFields,
  authFileTemplateFromForm,
  authFileTemplateToForm,
  type AuthFileTemplateForm,
} from "../../utils/AuthFileTemplate";

export function AccountPoolAuthFileFieldsDialog({
  accessToken,
  mode,
  targets,
  onClose,
  onSaved,
}: {
  accessToken: string;
  mode: "template" | "apply";
  targets: readonly { id: string; name: string }[];
  onClose: () => void;
  onSaved: (ids: readonly string[]) => void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: accountPoolQueryKeys.settings(accessToken),
    queryFn: () => getAccountPoolSettings(accessToken),
    retry: false,
  });
  const profiles = useQuery(accountPoolProxyProfileOptions(accessToken));
  const [draft, setDraft] = useState<AuthFileTemplateForm | null>(null);
  const form = draft ?? authFileTemplateToForm(mode === "template" ? settings.data?.values.auth_file_template : null);
  const [message, setMessage] = useState<string | null>(null);
  const [completed, setCompleted] = useState<readonly string[]>([]);
  const remaining = targets.filter((target) => !completed.includes(target.id));
  const update = (patch: Partial<AuthFileTemplateForm>) => {
    setDraft({ ...form, ...patch });
    setMessage(null);
  };
  const saveTemplate = useMutation({
    mutationFn: async () => {
      if (!settings.data) throw new Error(t("accountPool.credentialFields.reloadTheConfigurationAndTryAgain"));
      return updateAccountPoolSettings(
        accessToken,
        {
          version: settings.data.version,
          values: { ...settings.data.values, auth_file_template: authFileTemplateFromForm(form) },
        },
        "common",
      );
    },
    onSuccess: (saved) => {
      client.setQueryData(accountPoolQueryKeys.settings(accessToken), saved);
      toast.success(t("accountPool.credentialFields.defaultCredentialTemplateSaved"));
      setMessage(t("accountPool.credentialFields.defaultTemplateSavedExistingCredentialFiles"));
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const apply = useMutation({
    mutationFn: async () => {
      const fields = authFileTemplateFields(authFileTemplateFromForm(form));
      if (Object.keys(fields).length === 0) throw new Error(t("accountPool.credentialFields.selectTheFieldsToChange"));
      const succeeded: string[] = [];
      const failed: string[] = [];
      for (const target of remaining) {
        try {
          await patchAccountPoolAuthFileFields(accessToken, target.id, fields);
          succeeded.push(target.id);
        } catch {
          failed.push(target.name);
        }
      }
      return { succeeded, failed };
    },
    onSuccess: ({ succeeded, failed }) => {
      setCompleted((current) => [...current, ...succeeded]);
      onSaved(succeeded);
      if (failed.length === 0) {
        toast.success(t("accountPool.credentialFields.updatedCredentialEntries", { updated: succeeded.length }));
        onClose();
      } else {
        setMessage(t("accountPool.credentialFields.updatedEntriesFailedOnlyFailedEntries", { updated: succeeded.length, failed: failed.length, names: failed.join(t("accountPool.credentialFields.listSeparator")) }));
      }
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const busy = apply.isPending || saveTemplate.isPending;
  const fieldsLocked = busy || completed.length > 0;
  const missingProfile =
    form.applyProxy &&
    form.proxyProfileId !== "" &&
    !profiles.data?.some((profile) => profile.id === form.proxyProfileId);
  const selectedFields = [
    form.applyPriority && t("accountPool.policy.options.priority"),
    form.applyPrefix && t("accountPool.providers.openaiCompatible.prefix"),
    form.applyProxy && t("accountPool.providers.openaiCompatible.proxyProfileId"),
  ].filter(Boolean);

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] min-w-0 overflow-y-auto sm:max-w-xl" showCloseButton={!busy}>
        <DialogHeader className="min-w-0">
          <DialogTitle>{mode === "template" ? t("accountPool.credentialFields.credentialTemplate") : t("accountPool.credentials.editFields")}</DialogTitle>
          <DialogDescription className="break-words">
            {t("accountPool.credentialFields.selectTheFieldsToChangeUnselected")}</DialogDescription>
        </DialogHeader>
        <fieldset disabled={fieldsLocked} className="grid min-w-0 gap-4">
          <div className="grid gap-2">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={form.applyPriority}
                disabled={fieldsLocked}
                onCheckedChange={(checked) => update({ applyPriority: checked === true })}
              />
              {t("accountPool.credentialFields.changePriority")}</label>
            <Label htmlFor="auth-template-priority">{t("accountPool.policy.options.priority")}</Label>
            <Input
              id="auth-template-priority"
              type="number"
              min={-2147483648}
              max={2147483647}
              step={1}
              disabled={!form.applyPriority}
              value={form.priority}
              onChange={(event) => update({ priority: event.target.value })}
            />
          </div>
          <div className="grid gap-2">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={form.applyPrefix}
                disabled={fieldsLocked}
                onCheckedChange={(checked) => update({ applyPrefix: checked === true })}
              />
              {t("accountPool.credentialFields.changeModelPrefix")}</label>
            <Label htmlFor="auth-template-prefix">{t("accountPool.providers.openaiCompatible.prefix")}</Label>
            <Input
              id="auth-template-prefix"
              maxLength={256}
              disabled={!form.applyPrefix}
              value={form.prefix}
              onChange={(event) => update({ prefix: event.target.value })}
              placeholder={t("accountPool.credentialFields.leaveBlankToClearThePrefix")}
            />
          </div>
          <div className="grid gap-2">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={form.applyProxy}
                disabled={fieldsLocked}
                onCheckedChange={(checked) => update({ applyProxy: checked === true })}
              />
              {t("accountPool.credentialFields.changeOutboundProxy")}</label>
            <Label htmlFor="auth-template-proxy">{t("accountPool.credentialFields.credentialFileProxy")}</Label>
            <select
              id="auth-template-proxy"
              className="h-9 w-full min-w-0 rounded-md border bg-background px-3"
              value={form.proxyProfileId}
              disabled={!form.applyProxy || profiles.isPending}
              onChange={(event) => update({ proxyProfileId: event.target.value })}
            >
              <option value="">{t("accountPool.credentialFields.useCardProxyClearTheFile")}</option>
              {missingProfile && <option value={form.proxyProfileId}>{t("accountPool.credentialFields.theOriginalProxyIsUnavailableSelect")}</option>}
              {(profiles.data ?? []).map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
            {profiles.isError && (
              <p role="alert" className="text-sm text-destructive">
                {t("accountPool.credentialFields.couldNotLoadProxiesCloseThis")}</p>
            )}
            {missingProfile && (
              <p role="alert" className="text-sm text-destructive">
                {t("accountPool.credentialFields.selectAnAvailableProxy")}</p>
            )}
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={!settings.data?.values.auth_file_template}
            onClick={() => {
              setDraft(authFileTemplateToForm(settings.data?.values.auth_file_template));
              setMessage(null);
            }}
          >
            {t("accountPool.credentialFields.loadDefaultTemplate")}</Button>
        </fieldset>
        {mode === "apply" && (
          <div className="grid min-w-0 gap-1 rounded-md border bg-muted/20 p-3 text-sm" aria-label={t("accountPool.credentialFields.previewChanges")}>
            <p>
              {t("accountPool.credentialFields.previewSummary", { total: remaining.length, fields: selectedFields.join(t("accountPool.credentialFields.listSeparator")) || t("accountPool.credentialFields.noFieldsSelected") })}
            </p>
            <p className="max-h-24 overflow-y-auto break-all text-muted-foreground">
              {remaining.map((target) => target.name).join(t("accountPool.credentialFields.listSeparator"))}
            </p>
          </div>
        )}
        {settings.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("accountPool.credentialFields.couldNotLoadTheDefaultTemplate")}</p>
        )}
        {message && (
          <p role="status" className="break-words text-sm">
            {message}
          </p>
        )}
        <DialogFooter className="flex-wrap">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            {t("accountPool.cancel")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={fieldsLocked || !settings.data || Boolean(missingProfile)}
            onClick={() => {
              setMessage(null);
              saveTemplate.mutate();
            }}
          >
            {t("accountPool.credentialFields.saveAsDefaultTemplate")}</Button>
          {mode === "apply" && (
            <Button
              type="button"
              disabled={busy || Boolean(missingProfile) || selectedFields.length === 0 || remaining.length === 0}
              onClick={() => {
                setMessage(null);
                apply.mutate();
              }}
            >
              {targets.length === 1 ? t("accountPool.save") : t("accountPool.credentialFields.applyToEntries", { total: remaining.length })}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
