/** 本文件复用配置子模块的命名配置、继承切换和卡片唯一绑定交互。 */

"use client";

import type { ReactNode } from "react";
import { Plus, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

import type { AccountPoolEnvironment } from "./AccountPoolTypes";

export type SettingsProfile<TValues> = {
  id: string;
  name: string;
  card_ids: string[];
  inherit_global: boolean;
  values: TValues;
};

type Props<TValues> = {
  moduleName: string;
  globalValues: TValues;
  profiles: SettingsProfile<TValues>[];
  environments: readonly AccountPoolEnvironment[];
  busy: boolean;
  onGlobalChange: (values: TValues) => void;
  onProfilesChange: (profiles: SettingsProfile<TValues>[]) => void;
  onProfileDeleted: (profileId: string) => void;
  onSave: () => void;
  renderValues: (
    values: TValues,
    onChange: (values: TValues) => void,
    disabled: boolean,
    editorId: string,
  ) => ReactNode;
};

export const AccountPoolSettingsProfileSection = <TValues,>({
  moduleName,
  globalValues,
  profiles,
  environments,
  busy,
  onGlobalChange,
  onProfilesChange,
  onProfileDeleted,
  onSave,
  renderValues,
}: Props<TValues>) => {
  const { t } = useTranslation();
  const updateProfile = (index: number, update: Partial<SettingsProfile<TValues>>) =>
    onProfilesChange(profiles.map((profile, itemIndex) => (itemIndex === index ? { ...profile, ...update } : profile)));

  return (
    <div className="grid gap-5 pt-4">
      <section className="grid gap-4 rounded-lg border bg-muted/15 p-4">
        <div>
          <h3 className="text-sm font-semibold">{t("accountPool.settings.globalConfiguration")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.settings.globalConfigurationHint")}</p>
        </div>
        {renderValues(globalValues, onGlobalChange, busy, `${moduleName}-global`)}
      </section>

      <section className="grid gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">{t("accountPool.settings.namedConfigurations")}</h3>
            <p className="mt-1 text-xs text-muted-foreground">{t("accountPool.settings.namedConfigurationsHint")}</p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() =>
              onProfilesChange([
                ...profiles,
                {
                  id: crypto.randomUUID(),
                  name: `${moduleName} ${profiles.length + 1}`,
                  card_ids: [],
                  inherit_global: true,
                  values: globalValues,
                },
              ])
            }
          >
            <Plus className="size-4" />
            {t("accountPool.settings.addConfiguration")}
          </Button>
        </div>

        {profiles.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {t("accountPool.settings.noNamedConfigurations")}
          </div>
        ) : (
          <div className="grid gap-4">
            {profiles.map((profile, index) => {
              const assignedElsewhere = new Set(
                profiles.flatMap((item, itemIndex) => (itemIndex === index ? [] : item.card_ids)),
              );
              const availableCards = environments.filter(
                (environment) => !assignedElsewhere.has(environment.id) && !profile.card_ids.includes(environment.id),
              );
              return (
                <div key={profile.id} className="grid gap-4 rounded-lg border p-4 shadow-sm">
                  <div className="flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-end">
                    <div className="grid min-w-0 flex-1 gap-1.5">
                      <Label htmlFor={`${moduleName}-${profile.id}-name`}>
                        {t("accountPool.settings.configurationName")}
                      </Label>
                      <Input
                        id={`${moduleName}-${profile.id}-name`}
                        value={profile.name}
                        maxLength={120}
                        disabled={busy}
                        onChange={(event) => updateProfile(index, { name: event.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        onProfilesChange(profiles.filter((_, itemIndex) => itemIndex !== index));
                        onProfileDeleted(profile.id);
                      }}
                    >
                      <Trash2 className="size-4" />
                      {t("accountPool.settings.deleteConfiguration")}
                    </Button>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/20 p-3">
                    <div>
                      <Label htmlFor={`${moduleName}-${profile.id}-inherit`}>
                        {t("accountPool.settings.inheritGlobal")}
                      </Label>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {profile.inherit_global
                          ? t("accountPool.settings.inheritGlobalActive")
                          : t("accountPool.settings.customConfigurationActive")}
                      </p>
                    </div>
                    <Switch
                      id={`${moduleName}-${profile.id}-inherit`}
                      checked={profile.inherit_global}
                      disabled={busy}
                      onCheckedChange={(checked) =>
                        updateProfile(index, {
                          inherit_global: checked === true,
                          values: checked === true ? profile.values : globalValues,
                        })
                      }
                    />
                  </div>

                  {renderValues(
                    profile.inherit_global ? globalValues : profile.values,
                    (values) => updateProfile(index, { values }),
                    busy || profile.inherit_global,
                    `${moduleName}-${profile.id}`,
                  )}

                  <div className="grid gap-3 border-t pt-4">
                    <div className="grid gap-1.5">
                      <Label>{t("accountPool.settings.applyCards")}</Label>
                      <Select
                        value={null}
                        disabled={busy || availableCards.length === 0}
                        onValueChange={(cardId) => {
                          if (typeof cardId !== "string" || profile.card_ids.includes(cardId)) return;
                          updateProfile(index, { card_ids: [...profile.card_ids, cardId] });
                        }}
                      >
                        <SelectTrigger className="w-full" aria-label={t("accountPool.settings.selectCard")}>
                          <SelectValue placeholder={t("accountPool.settings.selectCard")} />
                        </SelectTrigger>
                        <SelectContent>
                          {availableCards.map((environment) => (
                            <SelectItem key={environment.id} value={environment.id}>
                              {environment.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {availableCards.length === 0 && (
                        <p className="text-xs text-muted-foreground">{t("accountPool.settings.noAvailableCards")}</p>
                      )}
                    </div>
                    {profile.card_ids.length === 0 ? (
                      <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                        {t("accountPool.settings.noAppliedCards")}
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {profile.card_ids.map((cardId) => {
                          const environment = environments.find((item) => item.id === cardId);
                          return (
                            <span
                              key={cardId}
                              className="inline-flex items-center gap-2 rounded-full border bg-background px-3 py-1.5 text-sm"
                            >
                              <span className="max-w-56 truncate">{environment?.name ?? cardId}</span>
                              <button
                                type="button"
                                className="rounded-full text-muted-foreground hover:text-foreground disabled:opacity-50"
                                disabled={busy}
                                onClick={() =>
                                  updateProfile(index, {
                                    card_ids: profile.card_ids.filter((identifier) => identifier !== cardId),
                                  })
                                }
                                aria-label={t("accountPool.settings.removeCard", {
                                  name: environment?.name ?? cardId,
                                })}
                              >
                                <X className="size-3.5" />
                              </button>
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <div className="flex justify-end border-t pt-4">
        <Button type="button" disabled={busy} onClick={onSave}>
          {t("accountPool.settings.save")}
        </Button>
      </div>
    </div>
  );
};
