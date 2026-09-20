/** 展示上号进度、人工接续和分页，密码及授权链接不进入查询缓存。 */
import { useEffect, useState } from "react";
import CopyButton from "@/components/shared/CopyButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import { AccountPoolAuthorizationPanel } from "../credentials/AccountPoolAuthorizationPanel";
import {
  actOnboarding,
  authorizeOnboarding,
  onboardingStates,
  revealOnboarding,
  type OnboardingAction,
  type OnboardingAuthorization,
  type OnboardingItem,
  type OnboardingSecrets,
} from "../../api/AccountPoolOnboardingApi";

const mailboxLinks = {
  outlook: "https://account.microsoft.com/security",
  gmail: "https://myaccount.google.com/security",
  mail: null,
};

export function AccountPoolOnboardingTasks({
  accessToken,
  items,
  onChange,
}: {
  accessToken: string;
  items: OnboardingItem[];
  onChange: () => Promise<void>;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [secret, setSecret] = useState<OnboardingSecrets | null>(null);
  const [authorization, setAuthorization] = useState<OnboardingAuthorization | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const selected = items.find((item) => item.id === selectedId);
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(page, pages);
  const visible = [...items].reverse().slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const close = () => {
    setSelectedId(null);
    setSecret(null);
    setAuthorization(null);
    setPassword("");
  };
  useEffect(() => {
    if (!secret) return;
    const timer = setTimeout(() => setSecret(null), 60000);
    return () => clearTimeout(timer);
  }, [secret]);
  const action = async (item: OnboardingItem, body: OnboardingAction) => {
    setBusy(true);
    try {
      await actOnboarding(accessToken, item.id, body);
      setSecret(null);
      setPassword("");
      if (body.action === "generate_password") setSecret(await revealOnboarding(accessToken, item.id));
      toast.success(body.action === "generate_password" ? "已生成建议密码，请实际改密成功后确认" : "任务已更新");
      await onChange();
    } catch {
      toast.error("操作未完成，后台可能正在处理，请刷新后重试");
    } finally {
      setBusy(false);
    }
  };
  const reveal = async () => {
    if (!selected) return;
    if (secret) {
      setSecret(null);
      return;
    }
    setBusy(true);
    try {
      setSecret(await revealOnboarding(accessToken, selected.id));
    } catch {
      toast.error("密码读取失败");
    } finally {
      setBusy(false);
    }
  };
  const showAuthorization = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      setAuthorization(await authorizeOnboarding(accessToken, selected.id));
    } catch {
      toast.error("暂无有效授权链接，请刷新任务或重试授权");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          暂无任务，先选择供应商并上传文件
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="p-3">账号 / 文件</th>
                  <th className="p-3">状态</th>
                  <th className="p-3">卡片与进度</th>
                  <th className="p-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id} className="border-t align-top">
                    <td className="p-3">
                      <p className="break-all font-medium">{item.label}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.supplier}
                        {item.mailbox ? ` · ${item.mailbox} · 密码 ********` : ""}
                      </p>
                    </td>
                    <td className="p-3">
                      <Badge variant={item.state === "failed" ? "destructive" : "secondary"}>
                        {onboardingStates[item.state]}
                      </Badge>
                    </td>
                    <td className="max-w-sm p-3">
                      <p>{item.card_name ?? "等待卡片验证"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{item.message}</p>
                      {item.models.length > 0 && <p className="mt-1 text-xs">{item.models.length} 个模型</p>}
                    </td>
                    <td className="p-3">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          close();
                          setSelectedId(item.id);
                        }}
                      >
                        查看 / 操作
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-end gap-3 text-sm">
            <span>
              共 {items.length} 项 · 第 {currentPage} / {pages} 页
            </span>
            <select
              aria-label="每页任务数"
              className="rounded-md border bg-background p-1"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              {[10, 25, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {size} 条 / 页
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>
              上一页
            </Button>
            <Input
              aria-label="跳转任务页"
              type="number"
              min={1}
              max={pages}
              value={currentPage}
              className="w-20"
              onChange={(event) => setPage(Math.max(1, Math.min(pages, Number(event.target.value) || 1)))}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === pages}
              onClick={() => setPage(currentPage + 1)}
            >
              下一页
            </Button>
          </div>
        </>
      )}
      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="break-all">{selected?.label}</DialogTitle>
            <DialogDescription>
              {selected ? `${onboardingStates[selected.state]} · ${selected.message}` : "任务详情"}
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="space-y-5">
              <div className="rounded-lg bg-muted/40 p-4 text-sm">
                <p>卡片：{selected.card_name ?? "等待创建或验证"}</p>
                <p className="mt-1 break-all text-xs text-muted-foreground">卡片 ID：{selected.card_id}</p>
                <p className="mt-1">已尝试 {selected.attempts} 次</p>
                {selected.models.length > 0 && <p className="mt-2 break-words">模型：{selected.models.join("、")}</p>}
              </div>
              {selected.source === "oauth" && (
                <div className="space-y-3 rounded-lg border p-4">
                  <div className="flex items-center justify-between">
                    <h4 className="font-medium">账号密码</h4>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void reveal()}>
                      {secret ? "隐藏密码" : "查看密码"}
                    </Button>
                  </div>
                  {(
                    [
                      ["mailbox_password", "邮箱密码"],
                      ["supplier_password", "供应商密码"],
                      ["proposed_password", "待确认的新密码"],
                    ] as const
                  ).map(([key, label]) => (
                    <div key={key} className="flex items-center gap-2 text-sm">
                      <span className="w-28 shrink-0">{label}</span>
                      <span className="min-w-0 flex-1 break-all font-mono">
                        {secret ? secret[key] || "未设置" : "********"}
                      </span>
                      {secret?.[key] && <CopyButton value={secret[key]!} label={`复制${label}`} />}
                    </div>
                  ))}
                  <p className="text-xs text-muted-foreground">显示 60 秒后自动隐藏。邮箱密码与供应商密码分别保存。</p>
                  {["awaiting_mailbox", "standby", "disabled", "failed"].includes(selected.state) && (
                    <div className="space-y-3 border-t pt-3">
                      <p className="text-sm">在邮箱官方页面完成改密后，确认下方密码。生成建议密码不会自动修改邮箱。</p>
                      {selected.mailbox && mailboxLinks[selected.mailbox] ? (
                        <a
                          className="text-sm text-primary underline"
                          href={mailboxLinks[selected.mailbox]!}
                          target="_blank"
                          rel="noreferrer"
                        >
                          打开邮箱安全设置
                        </a>
                      ) : (
                        <p className="text-sm text-muted-foreground">请前往该邮箱服务的官方账号设置页</p>
                      )}
                      <Button
                        className="ml-3"
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void action(selected, { action: "generate_password" })}
                      >
                        生成随机密码
                      </Button>
                      <Label htmlFor="confirmed-mailbox-password">实际使用的邮箱密码</Label>
                      <Input
                        id="confirmed-mailbox-password"
                        type="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        placeholder="填写实际密码；留空使用已生成或原密码"
                      />
                      <Button
                        disabled={busy}
                        onClick={() =>
                          void action(selected, { action: "mailbox_ready", mailbox_password: password || null })
                        }
                      >
                        我已验证邮箱登录，确认密码
                      </Button>
                    </div>
                  )}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {["standby", "failed", "disabled"].includes(selected.state) && (
                  <Button
                    disabled={busy}
                    onClick={() => void action(selected, { action: selected.state === "standby" ? "start" : "retry" })}
                  >
                    {selected.state === "standby" ? "启动授权" : "重试原任务"}
                  </Button>
                )}
                {selected.state === "awaiting_authorization" && (
                  <Button disabled={busy} onClick={() => void showAuthorization()}>
                    显示授权步骤
                  </Button>
                )}
                {["queued", "standby", "awaiting_mailbox", "awaiting_authorization"].includes(selected.state) && (
                  <Button disabled={busy} variant="outline" onClick={() => void action(selected, { action: "pause" })}>
                    暂停任务
                  </Button>
                )}
              </div>
              {authorization && selected.state === "awaiting_authorization" && (
                <AccountPoolAuthorizationPanel authorization={authorization} idPrefix={`onboarding-${selected.id}`} />
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
