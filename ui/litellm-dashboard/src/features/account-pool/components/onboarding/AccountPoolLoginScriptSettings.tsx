/** 支持发布单文件和目录脚本包，发布密钥不进缓存。 */
"use client";

import { getEmailBindingScript, updateEmailBindingScript } from "../../api/AccountPoolEmailRebindingApi";

import { useTranslation } from "react-i18next";
import { useId, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getAutomationLoginScript,
  updateAutomationLoginScript,
  type AutomationSettings,
  type LoginScriptStatus,
} from "../../api/AccountPoolAutomationApi";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

type SelectedFile = { file: File; path: string };
const maxBytes = 65_536;
const maxFiles = 64;
const isEntry = (path: string) => /\.(?:mjs|js)$/.test(path);

function encodeBase64(bytes: Uint8Array): string {
  const chunks: string[] = [];
  const step = 8192;
  for (let index = 0; index < bytes.length; index += step) {
    chunks.push(String.fromCharCode(...bytes.subarray(index, index + step)));
  }
  return btoa(chunks.join(""));
}

export function SettingsForm({
  settings,
  disabled,
  save,
}: {
  settings: AutomationSettings;
  disabled: boolean;
  save: (settings: AutomationSettings) => Promise<void>;
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "accountPool.automation.script" });
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
        {t("settingsHelp")}
      </p>
      <Label>
        <Checkbox checked={enabled} onCheckedChange={(value) => setEnabled(value === true)} disabled={disabled} />
        {t("enable")}
      </Label>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor="automation-total">{t("total")}</Label>
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
          <Label htmlFor="automation-human">{t("human")}</Label>
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
        {t("save")}
      </Button>
    </form>
  );
}

function scriptStatusLabel(value: LoginScriptStatus | undefined, failed: boolean,
  t: (key: string, options?: Record<string, string | number>) => string) {
  if (failed) return t("loadError");
  if (value?.invalid) return t("invalid");
  const active = value?.active;
  return active
    ? t("current", { entry: active.entrypoint, count: active.file_count, hash: active.sha256.slice(0, 12) })
    : t("builtin");
}

export function AccountPoolLoginScriptSettings({
  accessToken,
  kind = "login",
}: {
  accessToken: string;
  kind?: "login" | "rebinding";
}) {
  const { t } = useTranslation(undefined, { keyPrefix: "accountPool.automation.script" });
  const rebinding = kind === "rebinding";
  const id = useId();
  const client = useQueryClient();
  const queryKey = [
    ...accountPoolQueryKeys.automation(accessToken),
    rebinding ? "email-binding-script" : "login-script",
  ] as const;
  const status = useQuery({
    queryKey,
    queryFn: () => (rebinding ? getEmailBindingScript(accessToken) : getAutomationLoginScript(accessToken)),
    retry: false,
  });
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [entrypoint, setEntrypoint] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const choose = (selection: FileList | null) => {
    if (!selection?.length) return;
    const entries = Array.from(selection, (file) => ({
      file,
      path: file.webkitRelativePath?.split("/").slice(1).join("/") || file.name,
    }));
    setNotice("");
    if (entries.length > maxFiles || entries.reduce((sum, { file }) => sum + file.size, 0) > maxBytes) {
      setFiles([]);
      setEntrypoint("");
      setError(t("size"));
      return;
    }
    const available = entries.filter(({ path }) => isEntry(path));
    const entry = available.find(({ path }) => path === (rebinding ? "rebind.mjs" : "login.mjs")) ?? available[0];
    setFiles(entries);
    setEntrypoint(entry?.path ?? "");
    setError(entry ? "" : t("entryRequired"));
  };

  const submit = async (form: HTMLFormElement, reset: boolean) => {
    const keyInput = form.elements.namedItem("publisher-key");
    const entryMissing = !reset && !entrypoint;
    if (entryMissing || !(keyInput instanceof HTMLInputElement) || !keyInput.reportValidity()) return;
    const publisherKey = keyInput.value;
    keyInput.value = "";
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const bundle = reset
        ? null
        : {
            entrypoint,
            files: await Promise.all(
              files.map(async ({ file, path }) => {
                const bytes = new Uint8Array(await file.arrayBuffer());
                return { path, data: encodeBase64(bytes) };
              }),
            ),
          };
      const current = rebinding
        ? await updateEmailBindingScript(accessToken, publisherKey, bundle)
        : await updateAutomationLoginScript(accessToken, publisherKey, bundle);
      client.setQueryData(queryKey, current);
      const successNotice = rebinding ? t("publishedRebinding") : t("published");
      setNotice(reset ? t("reset") : successNotice);
      setFiles([]);
      setEntrypoint("");
      form.reset();
    } catch {
      setError(t("saveError"));
    } finally {
      setBusy(false);
    }
  };

  const publish = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit(event.currentTarget, false);
  };
  const occupied = status.data?.active;
  const disabled = busy || !status.data?.upload_enabled;
  const publishingNotConfigured = status.isSuccess && !status.data.upload_enabled;
  return (
    <section className="max-w-xl space-y-3 rounded-lg border p-4" aria-labelledby={`${id}-oauth-login-script-title`}>
      <h3 id={`${id}-oauth-login-script-title`} className="text-sm font-semibold">
        {rebinding ? t("emailScript") : t("browserScript")}
      </h3>
      <p className="break-all text-sm text-muted-foreground">
        {status.isPending ? t("loading") : scriptStatusLabel(status.data, status.isError, t)}
      </p>
      {publishingNotConfigured && (
        <p className="text-sm text-muted-foreground">{t("disabled")}</p>
      )}
      <p className="text-sm text-muted-foreground">
        {rebinding
          ? t("emailHelp")
          : t("loginHelp")}
      </p>
      {!publishingNotConfigured && (
        <form className="grid gap-3" onSubmit={publish}>
          <div className="grid gap-2">
            <Label htmlFor={`${id}-oauth-script-single`}>{t("upload")}</Label>
            <Input
              id={`${id}-oauth-script-single`}
              type="file"
              accept=".mjs,.js"
              disabled={disabled}
              onChange={(event) => choose(event.target.files)}
            />
            <Label htmlFor={`${id}-oauth-script-folder`}>{t("directory")}</Label>
            <Input
              id={`${id}-oauth-script-folder`}
              type="file"
              multiple
              {...{ webkitdirectory: "" }}
              disabled={disabled}
              onChange={(event) => choose(event.target.files)}
            />
          </div>
          {files.filter(({ path }) => isEntry(path)).length > 1 && (
            <div className="grid gap-2">
              <Label htmlFor={`${id}-oauth-script-entry`}>{t("entry", { count: files.length })}</Label>
              <select
                id={`${id}-oauth-script-entry`}
                className="h-9 rounded-md border bg-background px-3 text-sm"
                value={entrypoint}
                disabled={disabled}
                onChange={(event) => setEntrypoint(event.target.value)}
              >
                <option value="">{t("chooseEntry")}</option>
                {files
                  .filter(({ path }) => isEntry(path))
                  .map(({ path }) => (
                    <option key={path} value={path}>
                      {path}
                    </option>
                  ))}
              </select>
            </div>
          )}
          <Label htmlFor={`${id}-oauth-script-key`}>{t("publisherKey")}</Label>
          <Input
            id={`${id}-oauth-script-key`}
            name="publisher-key"
            type="password"
            autoComplete="off"
            required
            minLength={32}
            maxLength={128}
            disabled={disabled}
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={disabled || !entrypoint}>
              {t("publish")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || (!occupied && !status.data?.invalid)}
              onClick={(event) => {
                if (event.currentTarget.form) void submit(event.currentTarget.form, true);
              }}
            >
              {t("useBuiltin")}
            </Button>
          </div>
          {notice && (
            <p role="status" className="text-sm">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
