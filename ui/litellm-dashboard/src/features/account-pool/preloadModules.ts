/** 共享号池模块加载入口，供页面与登录预加载复用。 */
export const loadAccountPoolSettingsOverview = () =>
  import("@/features/account-pool/components/dashboard/AccountPoolSettingsOverview").then(
    (module) => module.AccountPoolSettingsOverview,
  );
export const loadAccountPoolCredentialsPanel = () =>
  import("@/features/account-pool/components/credentials/AccountPoolCredentialsPanel").then(
    (module) => module.AccountPoolCredentialsPanel,
  );
export const loadAccountPoolOnboardingPanel = () =>
  import("@/features/account-pool/components/onboarding/AccountPoolOnboardingPanel").then(
    (module) => module.AccountPoolOnboardingPanel,
  );
export const loadAccountPoolQuotaPanel = () =>
  import("@/features/account-pool/components/dashboard/AccountPoolQuotaPanel").then(
    (module) => module.AccountPoolQuotaPanel,
  );
export const loadAccountPoolUpstreamSyncPanel = () =>
  import("@/features/account-pool/components/upstream/AccountPoolUpstreamSyncPanel").then(
    (module) => module.AccountPoolUpstreamSyncPanel,
  );
export const loadAccountPoolPluginsPanel = () =>
  import("@/features/account-pool/components/plugins/AccountPoolPluginsPanel").then(
    (module) => module.AccountPoolPluginsPanel,
  );
export const loadAccountPoolReleasesPanel = () =>
  import("@/features/account-pool/components/releases/AccountPoolReleasesPanel").then(
    (module) => module.AccountPoolReleasesPanel,
  );
export const loadRuntimeSettingsSection = () =>
  import("@/components/Settings/RuntimeSettings/RuntimeSettingsSection").then(
    (module) => module.RuntimeSettingsSection,
  );
export const loadRuntimeConfigDialog = () =>
  import("@/components/Settings/RuntimeSettings/RuntimeConfigDialog").then((module) => module.RuntimeConfigDialog);
export const loadRuntimePolicyDialog = () =>
  import("@/components/Settings/RuntimeSettings/RuntimePolicyDialog").then((module) => module.RuntimePolicyDialog);
export const loadAccountPoolCreateDialog = () =>
  import("@/features/account-pool/components/cards/AccountPoolCreateDialog").then(
    (module) => module.AccountPoolCreateDialog,
  );
export const loadProxyManagerPanel = () =>
  import("@/features/account-pool/components/proxy/ProxyManagerPanel").then((module) => module.ProxyManagerPanel);

export const ACCOUNT_POOL_MODULE_LOADERS = [
  loadAccountPoolCredentialsPanel,
  loadAccountPoolQuotaPanel,
  loadRuntimePolicyDialog,
  loadRuntimeConfigDialog,
  loadAccountPoolSettingsOverview,
  loadAccountPoolOnboardingPanel,
  loadProxyManagerPanel,
  loadAccountPoolUpstreamSyncPanel,
  loadAccountPoolPluginsPanel,
  loadAccountPoolReleasesPanel,
  loadRuntimeSettingsSection,
  loadAccountPoolCreateDialog,
];

export const preloadAccountPoolModules = () => Promise.all(ACCOUNT_POOL_MODULE_LOADERS.map((load) => load()));
