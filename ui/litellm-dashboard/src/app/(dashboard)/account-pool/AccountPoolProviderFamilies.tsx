/** 本文件展示号池供应商家族及实例数量，创建动作由页面注入以保持权限边界。 */

"use client";

import { Boxes, CircleAlert, Plus, RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { listAccountPoolProviderFamilies, type AccountPoolProviderFamily } from "./AccountPoolApi";
import type { AccountPoolSupplier } from "./AccountPoolTypes";

interface AccountPoolProviderFamiliesProps {
  accessToken: string | null;
  onCreate: (supplier: AccountPoolSupplier) => void;
}

const SUPPORTED_SUPPLIERS: readonly AccountPoolSupplier[] = [
  "openai_compatible",
  "openai_codex",
  "anthropic_claude",
  "google_antigravity",
  "kimi",
  "xai",
  "gemini",
  "gemini_interactions",
  "vertex",
] as const;

const supplierFromFamily = (family: AccountPoolProviderFamily): AccountPoolSupplier | null =>
  family.supplier && SUPPORTED_SUPPLIERS.includes(family.supplier as AccountPoolSupplier)
    ? (family.supplier as AccountPoolSupplier)
    : null;

export const AccountPoolProviderFamilies = ({ accessToken, onCreate }: AccountPoolProviderFamiliesProps) => {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["account-pool", "provider-families", accessToken],
    queryFn: () => listAccountPoolProviderFamilies(accessToken!),
    enabled: accessToken !== null,
    retry: false,
    staleTime: 30_000,
  });

  if (query.isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {["one", "two", "three"].map((key) => (
          <Skeleton key={key} className="h-48 w-full" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-5">
        <div className="flex items-start gap-3">
          <CircleAlert className="mt-0.5 size-5 text-destructive" />
          <div>
            <p className="font-medium text-destructive">{t("accountPool.providers.loadFailed")}</p>
            <p className="mt-1 text-sm text-muted-foreground">{query.error.message}</p>
            <Button type="button" variant="outline" size="sm" className="mt-4" onClick={() => void query.refetch()}>
              <RefreshCw />
              {t("accountPool.retry")}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const families = query.data ?? [];
  return (
    <div className="grid gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t("accountPool.providers.title")}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("accountPool.providers.description")}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {families.map((family) => {
          const supplier = supplierFromFamily(family);
          return (
            <Card key={family.kind} className="flex h-full flex-col">
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <Boxes className="mt-0.5 size-5 shrink-0 text-primary" />
                    <div className="min-w-0">
                      <CardTitle className="truncate text-base">{family.display_name}</CardTitle>
                      <p className="mt-1 text-xs text-muted-foreground">{family.authentication}</p>
                    </div>
                  </div>
                  <Badge variant={family.available ? "secondary" : "outline"}>
                    {family.card_count} {t("accountPool.providers.instances")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="flex-1 text-sm text-muted-foreground">
                <p>{family.description}</p>
              </CardContent>
              <CardFooter className="flex items-center justify-between gap-3 pt-0">
                <span className="text-xs text-muted-foreground">
                  {family.available ? t("accountPool.providers.available") : t("accountPool.providers.pendingAdapter")}
                </span>
                <Button
                  type="button"
                  size="sm"
                  disabled={!family.available || supplier === null}
                  onClick={() => supplier && onCreate(supplier)}
                >
                  <Plus />
                  {t("accountPool.providers.create")}
                </Button>
              </CardFooter>
            </Card>
          );
        })}
      </div>
    </div>
  );
};
