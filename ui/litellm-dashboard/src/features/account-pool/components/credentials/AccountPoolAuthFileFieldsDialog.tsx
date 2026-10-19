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
      if (!settings.data) throw new Error("请先读取配置后重试");
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
      toast.success("默认认证模板已保存");
      setMessage("默认认证模板已保存，未修改现有认证文件");
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const apply = useMutation({
    mutationFn: async () => {
      const fields = authFileTemplateFields(authFileTemplateFromForm(form));
      if (Object.keys(fields).length === 0) throw new Error("请勾选需要修改的字段");
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
        toast.success(`已更新 ${succeeded.length} 项认证字段`);
        onClose();
      } else {
        setMessage(`本次成功 ${succeeded.length} 项，失败 ${failed.length} 项：${failed.join("、")}。重试只处理失败项`);
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
    form.applyPriority && "优先级",
    form.applyPrefix && "模型前缀",
    form.applyProxy && "出站代理",
  ].filter(Boolean);

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] min-w-0 overflow-y-auto sm:max-w-xl" showCloseButton={!busy}>
        <DialogHeader className="min-w-0">
          <DialogTitle>{mode === "template" ? "认证模板" : t("accountPool.credentials.editFields")}</DialogTitle>
          <DialogDescription className="break-words">
            勾选要修改的字段，未勾选的保持原值。账号身份和令牌不会改变
          </DialogDescription>
        </DialogHeader>
        <fieldset disabled={fieldsLocked} className="grid min-w-0 gap-4">
          <div className="grid gap-2">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={form.applyPriority}
                disabled={fieldsLocked}
                onCheckedChange={(checked) => update({ applyPriority: checked === true })}
              />
              修改优先级
            </label>
            <Label htmlFor="auth-template-priority">优先级</Label>
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
              修改模型前缀
            </label>
            <Label htmlFor="auth-template-prefix">模型前缀</Label>
            <Input
              id="auth-template-prefix"
              maxLength={256}
              disabled={!form.applyPrefix}
              value={form.prefix}
              onChange={(event) => update({ prefix: event.target.value })}
              placeholder="留空可清除前缀"
            />
          </div>
          <div className="grid gap-2">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={form.applyProxy}
                disabled={fieldsLocked}
                onCheckedChange={(checked) => update({ applyProxy: checked === true })}
              />
              修改出站代理
            </label>
            <Label htmlFor="auth-template-proxy">认证文件出站代理</Label>
            <select
              id="auth-template-proxy"
              className="h-9 w-full min-w-0 rounded-md border bg-background px-3"
              value={form.proxyProfileId}
              disabled={!form.applyProxy || profiles.isPending}
              onChange={(event) => update({ proxyProfileId: event.target.value })}
            >
              <option value="">沿用卡片代理（清除文件内代理）</option>
              {missingProfile && <option value={form.proxyProfileId}>原节点不可用，请重新选择</option>}
              {(profiles.data ?? []).map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
            {profiles.isError && (
              <p role="alert" className="text-sm text-destructive">
                代理读取失败，请关闭后重试
              </p>
            )}
            {missingProfile && (
              <p role="alert" className="text-sm text-destructive">
                请选择当前可用的代理节点
              </p>
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
            载入默认模板
          </Button>
        </fieldset>
        {mode === "apply" && (
          <div className="grid min-w-0 gap-1 rounded-md border bg-muted/20 p-3 text-sm" aria-label="修改预览">
            <p>
              将修改 {remaining.length} 项：{selectedFields.join("、") || "未选择字段"}
            </p>
            <p className="max-h-24 overflow-y-auto break-all text-muted-foreground">
              {remaining.map((target) => target.name).join("、")}
            </p>
          </div>
        )}
        {settings.isError && (
          <p role="alert" className="text-sm text-destructive">
            默认模板读取失败，无法保存模板，请关闭后重试
          </p>
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
            保存为默认模板
          </Button>
          {mode === "apply" && (
            <Button
              type="button"
              disabled={busy || Boolean(missingProfile) || selectedFields.length === 0 || remaining.length === 0}
              onClick={() => {
                setMessage(null);
                apply.mutate();
              }}
            >
              {targets.length === 1 ? t("accountPool.save") : `应用到 ${remaining.length} 项`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
