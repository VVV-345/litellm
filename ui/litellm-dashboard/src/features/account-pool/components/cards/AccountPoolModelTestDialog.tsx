/** 本文件提供卡片级模型连通性测试，不保存凭据或修改路由策略。 */
import { useState } from "react";
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
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { testAccountPoolModel, type AccountPoolModelTestResult } from "../../api/AccountPoolApi";

interface Props {
  accessToken: string;
  environment: { id: string; name: string; enabled_models: readonly string[] };
  onClose: () => void;
}

export function AccountPoolModelTestDialog({ accessToken, environment, onClose }: Props) {
  const { t } = useTranslation();
  const [model, setModel] = useState(environment.enabled_models[0] ?? "");
  const [mode, setMode] = useState<"chat" | "responses">("chat");
  const [stream, setStream] = useState(false);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<AccountPoolModelTestResult | null>(null);
  const [failed, setFailed] = useState(false);
  const run = async () => {
    if (pending || !model) return;
    setPending(true);
    setResult(null);
    setFailed(false);
    try {
      setResult(await testAccountPoolModel(accessToken, environment.id, { model, mode, stream }));
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("accountPool.modelTest.title", { name: environment.name })}</DialogTitle>
          <DialogDescription>{t("accountPool.modelTest.notice")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="card-test-model">{t("accountPool.modelTest.model")}</Label>
            <Select
              value={model}
              onValueChange={(value) => {
                if (value) setModel(value);
              }}
              disabled={pending}
            >
              <SelectTrigger id="card-test-model" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {environment.enabled_models.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="card-test-mode">{t("accountPool.modelTest.protocol")}</Label>
            <Select
              value={mode}
              onValueChange={(value) => {
                if (value === "chat" || value === "responses") setMode(value);
              }}
              disabled={pending}
            >
              <SelectTrigger id="card-test-mode" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="chat">Chat Completions</SelectItem>
                <SelectItem value="responses">Responses</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="card-test-stream">{t("accountPool.modelTest.stream")}</Label>
            <Switch id="card-test-stream" checked={stream} onCheckedChange={setStream} disabled={pending} />
          </div>
          {result && (
            <div role={result.success ? "status" : "alert"} className="rounded-md border p-3 text-sm">
              <p className="font-medium">
                {t(result.success ? "accountPool.modelTest.success" : "accountPool.modelTest.failure")} ·{" "}
                {result.elapsed_ms} ms
              </p>
              <p>{result.message}</p>
              {result.error_code && <p>{result.error_code}</p>}
            </div>
          )}
          {failed && (
            <p role="alert" className="text-sm text-destructive">
              {t("accountPool.modelTest.requestFailed")}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>
            {t("accountPool.cancel")}
          </Button>
          <Button onClick={() => void run()} disabled={pending || !model}>
            {t(pending ? "accountPool.modelTest.running" : "accountPool.modelTest.run")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
