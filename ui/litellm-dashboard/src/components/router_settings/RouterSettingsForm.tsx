import Link from "next/link";
import React from "react";
import LatencyBasedConfiguration from "./LatencyBasedConfiguration";
import ReliabilityRetriesSection from "./ReliabilityRetriesSection";
import RoutingStrategySelector from "./RoutingStrategySelector";
import TagFilteringToggle from "./TagFilteringToggle";
import { pageHref } from "@/utils/migratedPages";
import { useTranslation } from "react-i18next";

export interface RouterSettingsFormValue {
  routerSettings: { [key: string]: any };
  selectedStrategy: string | null;
  enableTagFiltering: boolean;
}

interface RouterSettingsFormProps {
  value: RouterSettingsFormValue;
  onChange: (value: RouterSettingsFormValue) => void;
  routerFieldsMetadata: { [key: string]: any };
  availableRoutingStrategies: string[];
  routingStrategyDescriptions: { [key: string]: string };
}

const RouterSettingsForm: React.FC<RouterSettingsFormProps> = ({
  value,
  onChange,
  routerFieldsMetadata,
  availableRoutingStrategies,
  routingStrategyDescriptions,
}) => {
  const { t } = useTranslation();
  const handleStrategyChange = (strategy: string) => {
    onChange({
      ...value,
      selectedStrategy: strategy,
    });
  };

  const handleTagFilteringToggle = (enabled: boolean) => {
    onChange({
      ...value,
      enableTagFiltering: enabled,
    });
  };

  return (
    <div className="w-full space-y-8 py-2">
      {/* Routing Settings Section */}
      <div className="space-y-6">
        <div className="max-w-3xl">
          <h3 className="text-sm font-medium text-foreground">{t("ui.Routing Settings")}</h3>
          <p className="text-xs text-muted-foreground mt-1">
            {t("ui.Configure how requests are routed to deployments")}
          </p>
        </div>

        {/* Routing Strategy */}
        {availableRoutingStrategies.length > 0 && (
          <RoutingStrategySelector
            selectedStrategy={value.selectedStrategy || value.routerSettings.routing_strategy || null}
            availableStrategies={availableRoutingStrategies}
            routingStrategyDescriptions={routingStrategyDescriptions}
            routerFieldsMetadata={routerFieldsMetadata}
            onStrategyChange={handleStrategyChange}
          />
        )}

        {/* Tag Filtering */}
        <TagFilteringToggle
          enabled={value.enableTagFiltering}
          routerFieldsMetadata={routerFieldsMetadata}
          onToggle={handleTagFilteringToggle}
        />
      </div>

      {/* Divider */}
      <div className="border-t border-border" />

      {/* Strategy-Specific Args - Show immediately after strategy if latency-based */}
      {value.selectedStrategy === "latency-based-routing" && (
        <LatencyBasedConfiguration routingStrategyArgs={value.routerSettings["routing_strategy_args"]} />
      )}

      {/* Other Settings */}
      <section className="space-y-2 rounded-xl border bg-muted/20 p-5">
        <h3 className="text-sm font-semibold">号池账号选择</h3>
        <p className="text-xs leading-5 text-muted-foreground">
          选卡策略、指定卡片顺序和会话粘性已统一到策略中心，这里不再重复编辑。
        </p>
        <Link href={pageHref("policy-center")} className="text-sm underline">
          前往策略中心
        </Link>
      </section>
      <ReliabilityRetriesSection routerSettings={value.routerSettings} routerFieldsMetadata={routerFieldsMetadata} />
    </div>
  );
};

export default RouterSettingsForm;
