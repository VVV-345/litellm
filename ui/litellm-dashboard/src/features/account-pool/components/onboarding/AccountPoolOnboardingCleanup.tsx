/** 仅清理当前上号来源的完成历史，确认后按完成时间筛选，不删除卡片或有效凭据。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
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
import { toast } from "@/lib/toast";
import { cleanupOnboarding, type OnboardingImport } from "../../api/AccountPoolOnboardingApi";

export function AccountPoolOnboardingCleanup({
  accessToken,
  source,
  onChange,
}: {
  accessToken: string;
  source: OnboardingImport["source"];
  onChange: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState("all");
  const [before, setBefore] = useState("");
  const cutoff = before ? new Date(before) : null;
  const valid = range === "all" || (cutoff !== null && Number.isFinite(cutoff.getTime()));
  const clear = useMutation({
    mutationFn: () =>
      cleanupOnboarding(accessToken, {
        source,
        ...(range === "before" && cutoff ? { completed_before: cutoff.toISOString() } : {}),
      }),
    onSuccess: async ({ cleared }) => {
      toast.success(`已清理 ${cleared} 条已完成记录，卡片和有效凭据保持不变`);
      setOpen(false);
      await onChange();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          clear.reset();
          setOpen(true);
        }}
      >
        清理已完成记录
      </Button>
      <Dialog open={open} onOpenChange={(value) => !clear.isPending && setOpen(value)}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto" showCloseButton={!clear.isPending}>
          <DialogHeader>
            <DialogTitle>清理已完成记录</DialogTitle>
            <DialogDescription>
              仅清理当前{source === "auth_file" ? "认证文件" : "OAuth"}
              任务的完成历史。保留卡片、有效凭据和去重记录，不处理进行中、失败或人工暂停的任务
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Label htmlFor="onboarding-cleanup-range">清理范围</Label>
            <select
              id="onboarding-cleanup-range"
              className="h-9 w-full min-w-0 rounded-md border bg-background px-3"
              disabled={clear.isPending}
              value={range}
              onChange={(event) => setRange(event.target.value)}
            >
              <option value="all">全部已完成记录</option>
              <option value="before">按完成时间清理</option>
            </select>
            {range === "before" && (
              <>
                <Label htmlFor="onboarding-cleanup-before">完成时间早于</Label>
                <Input
                  id="onboarding-cleanup-before"
                  type="datetime-local"
                  value={before}
                  disabled={clear.isPending}
                  onChange={(event) => setBefore(event.target.value)}
                />
              </>
            )}
            {clear.isError && (
              <p role="alert" className="text-sm text-destructive">
                清理未完成，请重试
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={clear.isPending} onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button disabled={!valid || clear.isPending} onClick={() => clear.mutate()}>
              {clear.isPending ? "正在清理…" : "确认清理"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
