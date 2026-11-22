/** 支持发布单文件和目录脚本包，发布密钥不进缓存。 */
"use client";

import { getEmailBindingScript, updateEmailBindingScript } from "../../api/AccountPoolEmailRebindingApi";

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

function scriptStatusLabel(value: LoginScriptStatus | undefined, failed: boolean) {
  if (failed) return "无法加载脚本设置，请刷新后重试";
  if (value?.invalid) return "当前脚本包损坏，请重新发布或切回内置脚本";
  const active = value?.active;
  return active
    ? `当前：${active.entrypoint} · ${active.file_count} 个文件 · ${active.sha256.slice(0, 12)}`
    : "当前：内置脚本";
}

export function AccountPoolLoginScriptSettings({
  accessToken,
  kind = "login",
}: {
  accessToken: string;
  kind?: "login" | "rebinding";
}) {
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
      setError("请选最多 64 个文件、总大小不超过 64 KiB 的脚本包");
      return;
    }
    const available = entries.filter(({ path }) => isEntry(path));
    const entry = available.find(({ path }) => path === (rebinding ? "rebind.mjs" : "login.mjs")) ?? available[0];
    setFiles(entries);
    setEntrypoint(entry?.path ?? "");
    setError(entry ? "" : "请选择包含一个 .mjs 或 .js 入口文件的目录");
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
      const successNotice = rebinding ? "已发布，下次换绑使用新脚本" : "已发布，下次启动自动授权时使用";
      setNotice(reset ? "已切回内置脚本，运行中的任务不受影响" : successNotice);
      setFiles([]);
      setEntrypoint("");
      form.reset();
    } catch {
      setError("未能保存。请检查发布密钥、入口、文件大小或 Manager 日志");
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
        {rebinding ? "邮箱换绑脚本" : "浏览器登录脚本"}
      </h3>
      <p className="break-all text-sm text-muted-foreground">
        {status.isPending ? "正在加载脚本设置" : scriptStatusLabel(status.data, status.isError)}
      </p>
      {publishingNotConfigured && (
        <p className="text-sm text-muted-foreground">上传未启用，请联系管理员开启脚本发布。当前脚本不受影响。</p>
      )}
      <p className="text-sm text-muted-foreground">
        {rebinding
          ? "选 rebind.mjs（也支持 .js），入口导出 rebindEmail；有辅助文件就上传整个目录，不用打包，发布时须填写脚本发布密钥。"
          : "独立脚本：选 login.mjs（也支持 .js）。有相对引用：选包含入口和依赖文件的目录，不用打包。入口须导出 automateLogin 和 verifyResumableAccount。"}
      </p>
      {!publishingNotConfigured && (
        <form className="grid gap-3" onSubmit={publish}>
          <div className="grid gap-2">
            <Label htmlFor={`${id}-oauth-script-single`}>上传单文件</Label>
            <Input
              id={`${id}-oauth-script-single`}
              type="file"
              accept=".mjs,.js"
              disabled={disabled}
              onChange={(event) => choose(event.target.files)}
            />
            <Label htmlFor={`${id}-oauth-script-folder`}>上传目录</Label>
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
              <Label htmlFor={`${id}-oauth-script-entry`}>入口文件（{files.length} 个文件）</Label>
              <select
                id={`${id}-oauth-script-entry`}
                className="h-9 rounded-md border bg-background px-3 text-sm"
                value={entrypoint}
                disabled={disabled}
                onChange={(event) => setEntrypoint(event.target.value)}
              >
                <option value="">选择入口</option>
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
          <Label htmlFor={`${id}-oauth-script-key`}>脚本发布密钥</Label>
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
              发布脚本
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || (!occupied && !status.data?.invalid)}
              onClick={(event) => {
                if (event.currentTarget.form) void submit(event.currentTarget.form, true);
              }}
            >
              切回内置脚本
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
