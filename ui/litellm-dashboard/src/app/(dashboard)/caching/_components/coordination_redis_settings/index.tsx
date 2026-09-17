import React, { useEffect, useMemo, useState } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { UiLoadingSpinner } from "@/components/ui/ui-loading-spinner";
import { toast } from "@/lib/toast";
import { StatusBadge } from "@/components/shared/table_cells/status_badge";
import {
  useCoordinationRedisSettings,
  useTestCoordinationRedisConnection,
  useUpdateCoordinationRedisSettings,
} from "@/app/(dashboard)/hooks/coordinationRedis/useCoordinationRedisSettings";
import CoordinationRedisFieldSection from "./CoordinationRedisFieldSection";
import CoordinationRedisTypeSelector from "./CoordinationRedisTypeSelector";
import { COORDINATION_FIELDS, CoordinationRedisType } from "./coordinationRedisFields";
import {
  buildCoordinationPayload,
  buildInitialValues,
  configuredSecretFields,
  CoordinationFormValues,
  inferRedisType,
  isFieldVisible,
  sourceBadge,
} from "./coordinationRedisUtils";
import { useTranslation } from "react-i18next";

const CoordinationRedisSettings: React.FC = () => {
  const { t } = useTranslation();
  const form = useForm<CoordinationFormValues>({ defaultValues: buildInitialValues({}) });
  const [selectedRedisType, setSelectedRedisType] = useState<CoordinationRedisType | null>(null);

  const { data, isLoading, isError } = useCoordinationRedisSettings();
  const updateSettings = useUpdateCoordinationRedisSettings();
  const testConnection = useTestCoordinationRedisConnection();

  const redisType = selectedRedisType ?? inferRedisType(data?.values ?? {});

  useEffect(() => {
    if (data) {
      form.reset(buildInitialValues(data.values));
    }
  }, [data, form]);

  useEffect(() => {
    if (isError) {
      toast.fromError(t("ui.Failed to load coordination Redis settings"));
    }
  }, [isError]);

  const validate = (): CoordinationFormValues | null => {
    const values = form.getValues();
    const failures = COORDINATION_FIELDS.filter((field) => isFieldVisible(field, redisType)).flatMap((field) => {
      const message = field.rules?.map((rule) => rule(values[field.name])).find((result) => result !== null);
      return message === undefined || message === null ? [] : [[field.name, message] as const];
    });

    form.clearErrors();
    failures.forEach(([name, message]) => form.setError(name, { message }));
    return failures.length > 0 ? null : values;
  };

  const handleTestConnection = async () => {
    const values = validate();
    if (values === null) {
      return;
    }

    try {
      const result = await testConnection.mutateAsync(buildCoordinationPayload(redisType, values));
      if (result.status === "healthy") {
        toast.success(t("ui.Coordination Redis connection test successful!"));
      } else {
        toast.fromError(`Connection test failed: ${result.error ?? "Unknown error"}`);
      }
    } catch (error) {
      toast.fromError(`Connection test failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    }
  };

  const handleSaveChanges = async () => {
    const values = validate();
    if (values === null) {
      return;
    }

    try {
      await updateSettings.mutateAsync(buildCoordinationPayload(redisType, values));
      toast.success(t("ui.Coordination Redis settings saved. Restart the proxy to apply them."));
    } catch {
      toast.fromError(t("ui.Failed to update coordination Redis settings"));
    }
  };

  const badge = sourceBadge(data?.source);
  const configuredSecrets = useMemo(() => configuredSecretFields(data?.values ?? {}), [data]);

  return (
    <div className="w-full space-y-8 py-2">
      <FormProvider {...form}>
        <form onSubmit={(event) => event.preventDefault()} className="space-y-6">
          <div className="max-w-3xl space-y-2">
            <div className="flex items-center gap-3">
              <h3 className="text-sm font-medium text-foreground">{t("ui.Coordination Redis")}</h3>
              {!isLoading && (
                <StatusBadge tone={badge.tone} label={badge.label} dataTestId="coordination-redis-source" />
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {t(
                "ui.Redis used to coordinate work across proxy pods: cross-pod rate limits, spend tracking, and the pod lock manager. It is configured independently of the response cache.",
              )}
            </p>
            <p className="text-xs text-muted-foreground">{badge.tooltip}</p>
            <p className="text-xs text-warning">{t("ui.Saved changes take effect on proxy restart.")}</p>
          </div>

          <CoordinationRedisTypeSelector redisType={redisType} onTypeChange={setSelectedRedisType} />

          <div className="pt-4 border-t border-border">
            <CoordinationRedisFieldSection
              title={t("ui.Connection Settings")}
              section="connection"
              redisType={redisType}
              configuredSecrets={configuredSecrets}
            />
          </div>

          {redisType === "cluster" && (
            <div className="pt-4 border-t border-border">
              <CoordinationRedisFieldSection
                title={t("ui.Cluster Configuration")}
                section="cluster"
                redisType={redisType}
                configuredSecrets={configuredSecrets}
                gridCols="grid-cols-1 gap-6"
              />
            </div>
          )}

          {redisType === "sentinel" && (
            <div className="pt-4 border-t border-border">
              <CoordinationRedisFieldSection
                title={t("ui.Sentinel Configuration")}
                section="sentinel"
                redisType={redisType}
                configuredSecrets={configuredSecrets}
              />
            </div>
          )}

          <div className="pt-4 border-t border-border">
            <CoordinationRedisFieldSection
              title={t("ui.SSL Settings")}
              section="ssl"
              redisType={redisType}
              configuredSecrets={configuredSecrets}
            />
          </div>
        </form>
      </FormProvider>

      <div className="border-t border-border pt-6 flex justify-end gap-3">
        <Button variant="outline" onClick={handleTestConnection} disabled={testConnection.isPending}>
          {testConnection.isPending && <UiLoadingSpinner className="size-4" />}
          {testConnection.isPending ? "Testing..." : "Test Connection"}
        </Button>
        <Button onClick={handleSaveChanges} disabled={updateSettings.isPending}>
          {updateSettings.isPending && <UiLoadingSpinner className="size-4" />}
          {updateSettings.isPending ? "Saving..." : "Save Changes"}
        </Button>
      </div>
    </div>
  );
};

export default CoordinationRedisSettings;
