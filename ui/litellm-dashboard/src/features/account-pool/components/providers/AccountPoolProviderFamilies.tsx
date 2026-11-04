/** 本文件展示号池供应商家族及实例数量，创建动作由页面注入以保持权限边界。 */

"use client";

import { useState } from "react";
import { CircleAlert, Plus, RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";

import { listAccountPoolProviderFamilies, type AccountPoolProviderFamily } from "../../api/AccountPoolApi";
import type { AccountPoolSupplier } from "../../utils/AccountPoolTypes";
import { AccountPoolSupplierLogo } from "./AccountPoolSupplierLogo";
import { accountPoolQueryKeys } from "../../hooks/accountPoolQueryKeys";

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
  const [selected, setSelected] = useState<string | null>(null);
  const query = useQuery({
    queryKey: accountPoolQueryKeys.providerFamilies(accessToken),
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
  const active = families.find((family) => family.kind === selected) ?? families[0];
  return (
    <Tabs value={active?.kind ?? ""} onValueChange={setSelected}>
      <TabsList
        variant="line"
        className="h-auto group-data-horizontal/tabs:h-auto w-full flex-wrap justify-start gap-1"
      >
        {families.map((family) => (
          <TabsTrigger key={family.kind} value={family.kind} className="flex-none gap-2 px-3 py-2">
            {supplierFromFamily(family) && (
              <AccountPoolSupplierLogo supplier={supplierFromFamily(family)!} className="size-4" />
            )}
            {family.display_name}
            <span className="text-xs tabular-nums text-muted-foreground">{family.card_count}</span>
          </TabsTrigger>
        ))}
      </TabsList>
      {active && (
        <TabsContent value={active.kind} className="mt-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/30 p-4">
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                {active.authentication}
                <Badge variant="outline">
                  {active.available ? t("accountPool.providers.available") : t("accountPool.providers.pendingAdapter")}
                </Badge>
              </div>
              <p className="text-sm text-muted-foreground">{active.description}</p>
            </div>
            <Button
              type="button"
              size="sm"
              disabled={!active.available || supplierFromFamily(active) === null}
              onClick={() => {
                const supplier = supplierFromFamily(active);
                if (supplier) onCreate(supplier);
              }}
            >
              <Plus />
              {t("accountPool.providers.create")}
            </Button>
          </div>
        </TabsContent>
      )}
    </Tabs>
  );
};
