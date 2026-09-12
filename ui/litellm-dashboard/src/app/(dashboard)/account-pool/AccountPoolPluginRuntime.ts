/** 本文件将 CLIProxyAPI 插件与商城响应解析为稳定的页面模型。 */

export type AccountPoolRuntimePlugin = {
  key: string;
  id: string;
  name: string;
  description: string;
  storeVersion: string;
  installedVersion: string;
  sourceId: string;
  sourceName: string;
  installed: boolean;
  enabled: boolean;
  effectiveEnabled: boolean;
  updateAvailable: boolean;
  configured: boolean;
  registered: boolean;
  installType: string;
};

export type AccountPoolPluginSourceError = {
  sourceId: string;
  sourceName: string;
  message: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

const readString = (value: unknown, fallback = "") => (typeof value === "string" ? value : fallback);

const readBoolean = (value: unknown, fallback = false) => (typeof value === "boolean" ? value : fallback);

const parsePlugins = (
  payload: Record<string, unknown> | undefined,
  origin: "installed" | "store",
): AccountPoolRuntimePlugin[] => {
  const values: unknown = payload?.plugins;
  if (!Array.isArray(values)) return [];
  return values.flatMap((value) => {
    if (!isRecord(value) || typeof value.id !== "string") return [];
    const metadata = isRecord(value.metadata) ? value.metadata : {};
    const sourceId = readString(value.source_id);
    const path = readString(value.path);
    const configured = readBoolean(value.configured);
    const registered = readBoolean(value.registered);
    const installed = readBoolean(value.installed, origin === "installed" && (path !== "" || registered));
    return [
      {
        key: readString(value.store_id, sourceId ? `${sourceId}/${value.id}` : value.id),
        id: value.id,
        name: readString(value.name, readString(metadata.name, value.id)),
        description: readString(value.description),
        storeVersion: origin === "store" ? readString(value.version) : "",
        installedVersion: readString(value.installed_version, readString(metadata.version)),
        sourceId,
        sourceName: readString(value.source_name, sourceId),
        installed,
        enabled: readBoolean(value.enabled),
        effectiveEnabled: readBoolean(value.effective_enabled),
        updateAvailable: readBoolean(value.update_available),
        configured,
        registered,
        installType: readString(value.install_type, "sidecar"),
      },
    ];
  });
};

export const accountPoolRuntimePlugins = (
  storePayload: Record<string, unknown> | undefined,
  installedPayload: Record<string, unknown> | undefined,
): AccountPoolRuntimePlugin[] => {
  const storePlugins = parsePlugins(storePayload, "store");
  const storeIds = new Set(storePlugins.map((plugin) => plugin.id));
  const installedOnly = parsePlugins(installedPayload, "installed").filter((plugin) => !storeIds.has(plugin.id));
  return [...storePlugins, ...installedOnly];
};

export const accountPoolPluginSourceErrors = (
  payload: Record<string, unknown> | undefined,
): AccountPoolPluginSourceError[] => {
  const values: unknown = payload?.source_errors;
  if (!Array.isArray(values)) return [];
  return values.flatMap((value) => {
    if (!isRecord(value) || typeof value.message !== "string") return [];
    return [
      {
        sourceId: readString(value.source_id),
        sourceName: readString(value.source_name, readString(value.source_id)),
        message: value.message,
      },
    ];
  });
};
