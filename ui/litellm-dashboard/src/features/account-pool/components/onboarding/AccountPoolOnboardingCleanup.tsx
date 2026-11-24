/** 仅清理当前上号来源的完成历史，确认后按完成时间筛选，不删除卡片或有效凭据。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
      toast.success(t("accountPool.onboarding.cleanup.success", { cleared }));
      setOpen(false);
      await onChange();
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const sourceLabel =
    source === "auth_file"
      ? t("accountPool.onboarding.cleanup.sourceAuthFile")
      : t("accountPool.onboarding.cleanup.sourceOAuth");
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
        {t("accountPool.onboarding.cleanup.trigger")}
      </Button>
      <Dialog open={open} onOpenChange={(value) => !clear.isPending && setOpen(value)}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto" showCloseButton={!clear.isPending}>
          <DialogHeader>
            <DialogTitle>{t("accountPool.onboarding.cleanup.title")}</DialogTitle>
            <DialogDescription>
              {t("accountPool.onboarding.cleanup.description", { sourceLabel })}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Label htmlFor="onboarding-cleanup-range">{t("accountPool.onboarding.cleanup.rangeLabel")}</Label>
            <select
              id="onboarding-cleanup-range"
              className="h-9 w-full min-w-0 rounded-md border bg-background px-3"
              disabled={clear.isPending}
              value={range}
              onChange={(event) => setRange(event.target.value)}
            >
              <option value="all">{t("accountPool.onboarding.cleanup.rangeAll")}</option>
              <option value="before">{t("accountPool.onboarding.cleanup.rangeBefore")}</option>
            </select>
            {range === "before" && (
              <>
                <Label htmlFor="onboarding-cleanup-before">{t("accountPool.onboarding.cleanup.beforeLabel")}</Label>
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
                {t("accountPool.onboarding.cleanup.error")}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={clear.isPending} onClick={() => setOpen(false)}>
              {t("accountPool.onboarding.cleanup.cancel")}
            </Button>
            <Button disabled={!valid || clear.isPending} onClick={() => clear.mutate()}>
              {clear.isPending
                ? t("accountPool.onboarding.cleanup.clearing")
                : t("accountPool.onboarding.cleanup.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
