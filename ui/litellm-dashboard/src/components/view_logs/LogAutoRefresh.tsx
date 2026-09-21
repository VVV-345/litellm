import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/switch";

export function LogAutoRefresh({
  enabled,
  paused,
  onChange,
}: {
  enabled: boolean;
  paused: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Switch checked={enabled} onCheckedChange={onChange} aria-label={t("ui.Live Tail")} />
      <span>{t("ui.Live Tail")}</span>
      {enabled && (
        <span>
          {paused
            ? t("ui.Auto-refresh paused while browsing history or details")
            : t("ui.Auto-refreshing every 15 seconds")}
        </span>
      )}
    </div>
  );
}
