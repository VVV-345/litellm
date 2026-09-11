/** 本文件展示凭据文件的脱敏状态和所属卡片，不返回任何令牌或完整配置。 */

import { FileKey2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";

import type { AccountPoolEnvironment } from "./AccountPoolTypes";
import {
  addAccountPoolCredential,
  deleteAccountPoolCredential,
  listAccountPoolCredentials,
} from "./AccountPoolManagementApi";

export const AccountPoolCredentialsPanel = ({
  accessToken,
  environments,
}: {
  accessToken: string | null;
  environments: readonly AccountPoolEnvironment[];
}) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [addCard, setAddCard] = useState<AccountPoolEnvironment | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [weight, setWeight] = useState("1");
  const query = useQuery({
    queryKey: ["account-pool", "credentials", accessToken],
    queryFn: () => listAccountPoolCredentials(accessToken!),
    enabled: accessToken !== null,
    retry: false,
  });
  const credentials = query.data ?? [];
  const addMutation = useMutation({
    mutationFn: () => {
      if (!addCard || !apiKey.trim()) throw new Error(t("accountPool.credentials.keyRequired"));
      return addAccountPoolCredential(accessToken!, addCard.id, {
        version: addCard.version,
        api_key: apiKey.trim(),
        weight: Math.max(1, Number.parseInt(weight, 10) || 1),
      });
    },
    onSuccess: () => {
      toast.success(t("accountPool.credentials.added"));
      setAddCard(null);
      setApiKey("");
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
      void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
    },
    onError: (error: Error) => toast.fromError(error),
  });
  const removeCredential = (credential: (typeof credentials)[number]) => {
    const environment = environments.find((item) => item.id === credential.card_id);
    const index = Number.parseInt(credential.auth_index ?? "", 10) - 1;
    if (!environment || !Number.isInteger(index) || index < 0) return;
    if (!window.confirm(t("accountPool.credentials.removeConfirm"))) return;
    void deleteAccountPoolCredential(accessToken!, environment.id, {
      version: environment.version,
      credential_index: index,
    })
      .then(() => {
        toast.success(t("accountPool.credentials.removed"));
        void queryClient.invalidateQueries({ queryKey: ["account-pool", "credentials", accessToken] });
        void queryClient.invalidateQueries({ queryKey: ["account-pool", "environments"] });
      })
      .catch((error: unknown) => toast.fromError(error));
  };
  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-3">
        <div>
        <h2 className="text-lg font-semibold">{t("accountPool.credentials.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.credentials.description")}</p>
        </div>
        {environments.some((environment) => environment.channel === "openai_compatible") && (
          <Button
            type="button"
            size="sm"
            onClick={() => setAddCard(environments.find((environment) => environment.channel === "openai_compatible") ?? null)}
          >
            <Plus />
            {t("accountPool.credentials.add")}
          </Button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {query.isError && <p role="alert">{t("accountPool.credentials.loadFailed")}</p>}
        {credentials.length === 0 && !query.isError && environments.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("accountPool.dashboard.empty")}</p>
        )}
        {credentials.map((credential) => (
          <Card key={credential.id}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <FileKey2 className="size-5 shrink-0 text-primary" />
                  <CardTitle className="truncate text-base">{credential.card_name}</CardTitle>
                </div>
                <Badge variant={credential.enabled ? "secondary" : "outline"}>{credential.status}</Badge>
              </div>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("accountPool.credentials.provider")}</span>
                <span>{t(`accountPool.supplier.${credential.supplier}`)}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("accountPool.credentials.models")}</span>
                <span>{credential.model_count}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-muted-foreground">{t("accountPool.credentials.kind")}</span>
                <span>{credential.kind}</span>
              </div>
              {credential.kind === "api_key" && (
                <Button type="button" variant="ghost" size="sm" onClick={() => removeCredential(credential)}>
                  <Trash2 />
                  {t("accountPool.credentials.remove")}
                </Button>
              )}
              <p className="text-xs text-muted-foreground">{t("accountPool.credentials.secretHint")}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <Dialog open={addCard !== null} onOpenChange={(open) => !open && setAddCard(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("accountPool.credentials.add")}</DialogTitle>
            <DialogDescription>{addCard?.name}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="grid gap-1">
              <Label htmlFor="account-pool-api-key">{t("accountPool.credentials.apiKey")}</Label>
              <Input id="account-pool-api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="account-pool-key-weight">{t("accountPool.credentials.weight")}</Label>
              <Input id="account-pool-key-weight" type="number" min={1} max={10000} value={weight} onChange={(event) => setWeight(event.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setAddCard(null)}>{t("accountPool.cancel")}</Button>
            <Button type="button" onClick={() => addMutation.mutate()} disabled={addMutation.isPending || !apiKey.trim()}>{t("accountPool.save")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};
