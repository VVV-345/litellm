export const loadModelInfoView = () => import("@/components/model_info_view");
export const loadTeamInfoView = () => import("@/components/team/TeamInfo");
export const loadAutoRoutersTabPanel = () =>
  import("@/app/(dashboard)/models-and-endpoints/panels/AutoRoutersTabPanel");
export const loadAddModelPanel = () => import("@/app/(dashboard)/models-and-endpoints/panels/AddModelPanel");
export const loadLlmCredentialsPanel = () =>
  import("@/app/(dashboard)/models-and-endpoints/panels/LlmCredentialsPanel");
export const loadPassThroughPanel = () => import("@/app/(dashboard)/models-and-endpoints/panels/PassThroughPanel");
export const loadHealthStatusPanel = () => import("@/app/(dashboard)/models-and-endpoints/panels/HealthStatusPanel");
export const loadModelRetrySettingsPanel = () =>
  import("@/app/(dashboard)/models-and-endpoints/panels/ModelRetrySettingsPanel");
export const loadModelGroupAliasPanel = () =>
  import("@/app/(dashboard)/models-and-endpoints/panels/ModelGroupAliasPanel");
export const loadAccessGroupBudgetsPanel = () =>
  import("@/app/(dashboard)/models-and-endpoints/panels/AccessGroupBudgetsPanel");
export const loadPriceDataPanel = () => import("@/app/(dashboard)/models-and-endpoints/panels/PriceDataPanel");
export const loadModelRuntimeConfiguration = () =>
  import("@/components/Settings/RuntimeSettings/ModelRuntimeConfiguration");

export const preloadModelsModules = () =>
  Promise.all([
    loadModelInfoView(),
    loadTeamInfoView(),
    loadAutoRoutersTabPanel(),
    loadAddModelPanel(),
    loadLlmCredentialsPanel(),
    loadPassThroughPanel(),
    loadHealthStatusPanel(),
    loadModelRetrySettingsPanel(),
    loadModelGroupAliasPanel(),
    loadAccessGroupBudgetsPanel(),
    loadPriceDataPanel(),
    loadModelRuntimeConfiguration(),
  ]);
