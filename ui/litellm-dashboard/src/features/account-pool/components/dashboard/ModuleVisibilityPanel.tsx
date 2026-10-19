"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getUISettings, updateUiSettings } from "@/components/networking";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DEFAULT_DISABLED_PAGES,
  MODULE_OPTIONS,
  disabledPagesOrDefault,
  setPageEnabled,
} from "@/lib/moduleVisibility";

const MODULE_SETTINGS_QUERY_KEY = ["uiSettings", "module-visibility"] as const;

export function ModuleVisibilityPanel({ accessToken }: { accessToken: string }) {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: [...MODULE_SETTINGS_QUERY_KEY, accessToken],
    queryFn: () => getUISettings(accessToken),
    staleTime: 0,
  });
  const storedDisabled = disabledPagesOrDefault(settings.data?.values?.disabled_ui_pages);
  const storedKey = storedDisabled.join("|");
  const [draft, setDraft] = useState<{ key: string; disabled: readonly string[] } | null>(null);
  const disabled = draft?.key === storedKey ? draft.disabled : storedDisabled;
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const groups = useMemo(() => [...new Set(MODULE_OPTIONS.map((option) => option.group))], []);

  const toggle = (page: string, enabled: boolean) => {
    setDraft((current) => ({
      key: storedKey,
      disabled: setPageEnabled(current?.key === storedKey ? current.disabled : storedDisabled, page, enabled),
    }));
  };

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await updateUiSettings(accessToken, { disabled_ui_pages: [...disabled] });
      await queryClient.invalidateQueries({ queryKey: ["uiSettings"] });
      setMessage("已保存，侧边栏已刷新");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rounded-2xl border bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">模块开关</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            关闭的模块不会出现在侧边栏，也不会参与预加载或发起初始请求。打开后首次进入才加载。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDraft({ key: storedKey, disabled: DEFAULT_DISABLED_PAGES })}
            disabled={saving}
          >
            恢复默认
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={saving || settings.isPending}>
            {saving ? "正在保存" : "保存"}
          </Button>
        </div>
      </div>
      {settings.isError && (
        <p role="status" className="mt-3 text-sm text-destructive">
          读取失败，当前使用默认关闭列表
        </p>
      )}
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {groups.map((group) => (
          <fieldset key={group} className="rounded-xl border p-4">
            <legend className="px-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {group}
            </legend>
            <div className="mt-2 space-y-2">
              {MODULE_OPTIONS.filter((option) => option.group === group).map((option) => {
                const checkboxId = `module-visibility-${option.page}`;
                return (
                  <label key={option.page} htmlFor={checkboxId} className="flex cursor-pointer items-center gap-2">
                    <Checkbox
                      id={checkboxId}
                      checked={!disabled.includes(option.page)}
                      disabled={saving}
                      onCheckedChange={(checked) => toggle(option.page, checked === true)}
                    />
                    <span className="text-sm">{option.label}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
      {message && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {message}
        </p>
      )}
    </section>
  );
}
