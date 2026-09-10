import React, { useState, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { cx } from "@/lib/cva.config";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { UiLoadingSpinner } from "@/components/ui/ui-loading-spinner";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { toast } from "@/lib/toast";
import { ArrowLeft } from "lucide-react";
import { getAgentInfo, patchAgentCall, getAgentCreateMetadata, AgentCreateInfo } from "@/components/networking";
import { Agent } from "@/components/agents/types";
import { KeyResponse } from "@/components/key_team_helpers/key_list";
import { useKeys } from "@/app/(dashboard)/hooks/keys/useKeys";
import KeyInfoView from "@/components/templates/key_info_view";
import AgentVirtualKeys from "./agent_virtual_keys";
import AgentFormFields, { unmountedA2AFieldNames } from "./agent_form_fields";
import DynamicAgentFormFields, { buildDynamicAgentData, unmountedDynamicFieldNames } from "./dynamic_agent_form_fields";
import { AGENT_FORM_CONFIG, buildAgentDataFromForm, parseAgentForForm } from "./agent_config";
import {
  AgentFormField,
  AgentFormValues,
  AgentNumberInput,
  AgentRequestPayload,
  omitFieldValues,
  useCollapsiblePanels,
} from "./AgentFormKit";
import AgentCostView from "./agent_cost_view";
import { detectAgentType, parseDynamicAgentForForm } from "./agent_type_utils";
import AgentCardDiscovery, { DiscoveredAgentCardSelection } from "./agent_card_discovery";
import { buildDiscoveryRequest, overlayDiscoveredCardParams } from "./agent_discovery_utils";

interface AgentInfoViewProps {
  agentId: string;
  onClose: () => void;
  accessToken: string | null;
  isAdmin: boolean;
}

const DetailList: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className }) => (
  <dl
    className={cx(
      "grid grid-cols-[minmax(0,14rem)_minmax(0,1fr)] overflow-hidden rounded-lg border border-border text-sm",
      className,
    )}
  >
    {children}
  </dl>
);

const DetailItem: React.FC<{ label: React.ReactNode; children: React.ReactNode }> = ({ label, children }) => (
  <>
    <dt className="border-b border-border bg-muted px-4 py-3 font-medium text-foreground last-of-type:border-b-0">
      {label}
    </dt>
    <dd className="border-b border-border px-4 py-3 break-words text-foreground last-of-type:border-b-0">{children}</dd>
  </>
);

const AgentInfoView: React.FC<AgentInfoViewProps> = ({ agentId, onClose, accessToken, isAdmin }) => {
  const { t } = useTranslation();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [selectedKey, setSelectedKey] = useState<KeyResponse | null>(null);
  const { data: keysData, isLoading: keysLoading, refetch: refetchAgentKeys } = useKeys(1, 100, { agentID: agentId });
  const agentKeys = keysData?.keys ?? [];
  const [isLoading, setIsLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");
  const [isSaving, setIsSaving] = useState(false);
  const form = useForm<AgentFormValues>({ defaultValues: {} });
  const panels = useCollapsiblePanels([AGENT_FORM_CONFIG.basic.key]);
  const [agentTypeMetadata, setAgentTypeMetadata] = useState<AgentCreateInfo[]>([]);
  const [detectedAgentType, setDetectedAgentType] = useState<string>("a2a");
  const [appliedDiscoveredSelection, setAppliedDiscoveredSelection] = useState<DiscoveredAgentCardSelection | null>(
    null,
  );

  useEffect(() => {
    const fetchMetadata = async () => {
      try {
        const metadata = await getAgentCreateMetadata();
        setAgentTypeMetadata(metadata);
      } catch (error) {
        console.error("Error fetching agent metadata:", error);
      }
    };
    fetchMetadata();
  }, []);

  useEffect(() => {
    fetchAgentInfo();
  }, [agentId, accessToken]);

  const fetchAgentInfo = async () => {
    if (!accessToken) return;

    setIsLoading(true);
    try {
      const data = await getAgentInfo(accessToken, agentId);
      setAgent(data);

      // Detect agent type
      const agentType = detectAgentType(data);
      setDetectedAgentType(agentType);

      // Parse form values based on agent type
      if (agentType === "a2a") {
        form.reset(parseAgentForForm(data));
      } else {
        const typeInfo = agentTypeMetadata.find((t) => t.agent_type === agentType);
        if (typeInfo) {
          form.reset(parseDynamicAgentForForm(data, typeInfo));
        } else {
          form.reset(parseAgentForForm(data));
        }
      }
    } catch (error) {
      console.error("Error fetching agent info:", error);
      toast.error(t("ui.Failed to load agent information"));
    } finally {
      setIsLoading(false);
    }
  };

  // Re-parse form when metadata is loaded
  useEffect(() => {
    if (agent && agentTypeMetadata.length > 0) {
      const agentType = detectAgentType(agent);
      if (agentType !== "a2a") {
        const typeInfo = agentTypeMetadata.find((t) => t.agent_type === agentType);
        if (typeInfo) {
          form.reset(parseDynamicAgentForForm(agent, typeInfo));
        }
      }
    }
  }, [agentTypeMetadata, agent]);

  const selectedAgentTypeInfo = agentTypeMetadata.find((t) => t.agent_type === detectedAgentType);
  const watchedFormValues = useWatch({ control: form.control });

  const discoveryRequest = useMemo(
    () => buildDiscoveryRequest(detectedAgentType, watchedFormValues || {}, selectedAgentTypeInfo),
    [watchedFormValues, selectedAgentTypeInfo, detectedAgentType],
  );

  const handleApplyDiscoveredCard = (selection: DiscoveredAgentCardSelection | null) => {
    setAppliedDiscoveredSelection(selection);
    if (!selection) return;
    const { selected_card } = selection;
    const skills = (selected_card.skills ?? []).map((s) => ({
      id: s.id ?? "",
      name: s.name ?? "",
      description: s.description ?? "",
      tags: s.tags ?? [],
      examples: s.examples ?? [],
    }));

    const urlCredentialKeys = (selectedAgentTypeInfo?.credential_fields ?? [])
      .map((f) => f.key)
      .filter((key) => /(^|_)(url|api_base|endpoint)$/i.test(key));

    const fieldsToSet: AgentFormValues = {
      name: selected_card.name,
      description: selected_card.description,
      url: selection.upstream_url,
      streaming: Boolean(selected_card.capabilities?.streaming),
      skills,
      iconUrl: selected_card.iconUrl,
      documentationUrl: selected_card.documentationUrl,
      ...Object.fromEntries(urlCredentialKeys.map((key) => [key, selection.upstream_url])),
    };

    for (const [key, value] of Object.entries(fieldsToSet)) {
      form.setValue(key, value);
    }
  };

  const usesDynamicFields = detectedAgentType !== "a2a" && selectedAgentTypeInfo !== undefined;

  const handleUpdate = async (submitted: AgentFormValues) => {
    if (!accessToken || !agent) return;

    setIsSaving(true);
    try {
      const values = omitFieldValues(
        submitted,
        usesDynamicFields
          ? unmountedDynamicFieldNames(panels.mountedPanels)
          : unmountedA2AFieldNames(panels.mountedPanels),
      );

      const built: AgentRequestPayload = usesDynamicFields
        ? { ...buildDynamicAgentData(values, selectedAgentTypeInfo), agent_name: values.agent_name }
        : buildAgentDataFromForm(values, agent);

      const updateData = appliedDiscoveredSelection
        ? overlayDiscoveredCardParams(built, appliedDiscoveredSelection.selected_card)
        : built;

      await patchAgentCall(accessToken, agentId, updateData);
      toast.success(t("ui.Agent updated successfully"));
      setIsEditing(false);
      fetchAgentInfo();
    } catch (error) {
      console.error("Error updating agent:", error);
      toast.error(t("ui.Failed to update agent"));
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-4">
        <div className="flex justify-center items-center h-64">
          <UiLoadingSpinner className="size-8 text-primary" />
        </div>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="p-4">
        <div className="text-center">{t("ui.Agent not found")}</div>
        <Button onClick={onClose} className="mt-4">
          Back to Agents List
        </Button>
      </div>
    );
  }

  // Format date helper function
  const formatDate = (dateString?: string) => {
    if (!dateString) return "-";
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  const rateLimitField = (name: keyof AgentFormValues & string, label: string) => (
    <AgentFormField name={name} label={label}>
      {({ value, onChange, ref, ...control }) => (
        <AgentNumberInput
          {...control}
          value={value}
          onChange={onChange}
          inputRef={ref}
          min={0}
          placeholder={t("ui.Unlimited")}
        />
      )}
    </AgentFormField>
  );

  if (selectedKey) {
    return (
      <KeyInfoView
        keyId={selectedKey.token}
        keyData={selectedKey}
        onClose={() => setSelectedKey(null)}
        onDelete={() => {
          setSelectedKey(null);
          refetchAgentKeys();
        }}
        teams={null}
        backButtonText={t("ui.Back to Agent")}
      />
    );
  }

  return (
    <div className="p-4">
      <div>
        <Button variant="ghost" onClick={onClose} className="mb-4">
          <ArrowLeft className="size-4" />
          Back to Agents
        </Button>
        <h1 className="text-2xl font-semibold">{agent.agent_name || t("ui.Unnamed Agent")}</h1>
        <p className="text-sm text-muted-foreground font-mono">{agent.agent_id}</p>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList variant="line" className="mb-4 h-auto w-full justify-start rounded-none border-b p-0">
          <TabsTrigger value="overview" className="flex-none rounded-none px-4 py-2">
            Overview
          </TabsTrigger>
          {isAdmin && (
            <TabsTrigger value="settings" className="flex-none rounded-none px-4 py-2">
              Settings
            </TabsTrigger>
          )}
        </TabsList>

        <div>
          {/* Overview Panel */}
          <TabsContent value="overview" keepMounted>
            <DetailList>
              <DetailItem label={t("ui.Agent ID")}>{agent.agent_id}</DetailItem>
              <DetailItem label={t("ui.Agent Name")}>{agent.agent_name}</DetailItem>
              <DetailItem label={t("ui.Display Name")}>{agent.agent_card_params?.name || "-"}</DetailItem>
              <DetailItem label={t("ui.Description")}>{agent.agent_card_params?.description || "-"}</DetailItem>
              <DetailItem label={t("ui.URL")}>{agent.agent_card_params?.url || "-"}</DetailItem>
              <DetailItem label={t("ui.Version")}>{agent.agent_card_params?.version || "-"}</DetailItem>
              <DetailItem label={t("ui.Protocol Version")}>
                {agent.agent_card_params?.protocolVersion || "-"}
              </DetailItem>
              <DetailItem label={t("ui.Streaming")}>
                {agent.agent_card_params?.capabilities?.streaming ? t("ui.Yes") : t("ui.No")}
              </DetailItem>
              {agent.agent_card_params?.capabilities?.pushNotifications && (
                <DetailItem label={t("ui.Push Notifications")}>{t("ui.Yes")}</DetailItem>
              )}
              {agent.agent_card_params?.capabilities?.stateTransitionHistory && (
                <DetailItem label={t("ui.State Transition History")}>{t("ui.Yes")}</DetailItem>
              )}
              <DetailItem label={t("ui.Skills")}>
                {agent.agent_card_params?.skills?.length || 0} {t("ui.configured")}
              </DetailItem>
              {agent.litellm_params?.model && (
                <DetailItem label={t("ui.Model")}>{agent.litellm_params.model}</DetailItem>
              )}
              {agent.litellm_params?.make_public !== undefined && (
                <DetailItem label={t("ui.Make Public")}>
                  {agent.litellm_params.make_public ? t("ui.Yes") : t("ui.No")}
                </DetailItem>
              )}
              {agent.agent_card_params?.iconUrl && (
                <DetailItem label={t("ui.Icon URL")}>{agent.agent_card_params.iconUrl}</DetailItem>
              )}
              {agent.agent_card_params?.documentationUrl && (
                <DetailItem label={t("ui.Documentation URL")}>{agent.agent_card_params.documentationUrl}</DetailItem>
              )}
              <DetailItem label={t("ui.TPM Limit")}>{agent.tpm_limit ?? t("ui.Unlimited")}</DetailItem>
              <DetailItem label={t("ui.RPM Limit")}>{agent.rpm_limit ?? t("ui.Unlimited")}</DetailItem>
              <DetailItem label={t("ui.Session TPM Limit")}>{agent.session_tpm_limit ?? t("ui.Unlimited")}</DetailItem>
              <DetailItem label={t("ui.Session RPM Limit")}>{agent.session_rpm_limit ?? t("ui.Unlimited")}</DetailItem>
              <DetailItem label={t("ui.Created At")}>{formatDate(agent.created_at)}</DetailItem>
              <DetailItem label={t("ui.Updated At")}>{formatDate(agent.updated_at)}</DetailItem>
            </DetailList>

            <AgentVirtualKeys keys={agentKeys} isLoading={keysLoading} onKeyClick={setSelectedKey} />

            {agent.object_permission &&
              (agent.object_permission.mcp_servers?.length ||
                agent.object_permission.mcp_access_groups?.length ||
                (agent.object_permission.mcp_tool_permissions &&
                  Object.keys(agent.object_permission.mcp_tool_permissions).length > 0)) && (
                <div style={{ marginTop: 24 }}>
                  <h3 className="text-lg font-medium">{t("ui.MCP Tool Permissions")}</h3>
                  <DetailList className="mt-4">
                    {agent.object_permission.mcp_servers && agent.object_permission.mcp_servers.length > 0 && (
                      <DetailItem label={t("ui.MCP Servers")}>
                        {agent.object_permission.mcp_servers.join(", ")}
                      </DetailItem>
                    )}
                    {agent.object_permission.mcp_access_groups &&
                      agent.object_permission.mcp_access_groups.length > 0 && (
                        <DetailItem label={t("ui.MCP Access Groups")}>
                          {agent.object_permission.mcp_access_groups.join(", ")}
                        </DetailItem>
                      )}
                    {agent.object_permission.mcp_tool_permissions &&
                      Object.keys(agent.object_permission.mcp_tool_permissions).length > 0 && (
                        <DetailItem label={t("ui.Tool permissions per server")}>
                          <div className="space-y-1">
                            {Object.entries(agent.object_permission.mcp_tool_permissions).map(([serverId, tools]) => (
                              <div key={serverId}>
                                <span className="font-medium">{serverId}:</span>{" "}
                                {Array.isArray(tools) ? tools.join(", ") : String(tools)}
                              </div>
                            ))}
                          </div>
                        </DetailItem>
                      )}
                  </DetailList>
                </div>
              )}

            <AgentCostView agent={agent} />

            {agent.agent_card_params?.skills && agent.agent_card_params.skills.length > 0 && (
              <div style={{ marginTop: 24 }}>
                <h3 className="text-lg font-medium">{t("ui.Skills")}</h3>
                <DetailList className="mt-4">
                  {agent.agent_card_params.skills.map((skill: any, index: number) => (
                    <DetailItem label={skill.name || t("ui.Skill {{count}}", { count: index + 1 })} key={index}>
                      <div>
                        <div>
                          <strong>{t("ui.ID")}:</strong> {skill.id}
                        </div>
                        <div>
                          <strong>{t("ui.Description")}:</strong> {skill.description}
                        </div>
                        <div>
                          <strong>{t("ui.Tags")}:</strong>{" "}
                          {Array.isArray(skill.tags) ? skill.tags.join(", ") : skill.tags}
                        </div>
                        {skill.examples && skill.examples.length > 0 && (
                          <div>
                            <strong>{t("ui.Examples")}:</strong>{" "}
                            {Array.isArray(skill.examples) ? skill.examples.join(", ") : skill.examples}
                          </div>
                        )}
                      </div>
                    </DetailItem>
                  ))}
                </DetailList>
              </div>
            )}
          </TabsContent>

          {/* Settings Panel (only for admins) */}
          {isAdmin && (
            <TabsContent value="settings" keepMounted>
              <Card className="block p-6">
                <div className="flex justify-between items-center mb-4">
                  <h3 className="text-lg font-medium">{t("ui.Agent Settings")}</h3>
                  {!isEditing && (
                    <Button
                      onClick={() => {
                        setAppliedDiscoveredSelection(null);
                        setIsEditing(true);
                      }}
                    >
                      Edit Settings
                    </Button>
                  )}
                </div>

                {isEditing ? (
                  <TooltipProvider>
                    <FormProvider {...form}>
                      <form onSubmit={form.handleSubmit(handleUpdate)}>
                        <FieldGroup className="mb-4">
                          <Field>
                            <FieldLabel htmlFor="agent-id">{t("ui.Agent ID")}</FieldLabel>
                            <Input id="agent-id" value={agent.agent_id} disabled readOnly />
                          </Field>
                        </FieldGroup>

                        {usesDynamicFields && selectedAgentTypeInfo ? (
                          <DynamicAgentFormFields agentTypeInfo={selectedAgentTypeInfo} panels={panels} />
                        ) : (
                          <AgentFormFields showAgentName={true} panels={panels} />
                        )}

                        {discoveryRequest && (
                          <div className="mt-4">
                            <AgentCardDiscovery
                              accessToken={accessToken}
                              onApply={handleApplyDiscoveredCard}
                              discoveryRequest={discoveryRequest}
                              savedAgentCard={agent.agent_card_params ?? null}
                            />
                          </div>
                        )}

                        <Separator className="my-6" />
                        <h3 className="text-lg font-medium mb-4">{t("ui.Rate Limits")}</h3>
                        <div className="grid grid-cols-2 gap-4">
                          {rateLimitField("tpm_limit", t("ui.TPM Limit"))}
                          {rateLimitField("rpm_limit", t("ui.RPM Limit"))}
                        </div>
                        <div className="mt-4 grid grid-cols-2 gap-4">
                          {rateLimitField("session_tpm_limit", t("ui.Session TPM Limit"))}
                          {rateLimitField("session_rpm_limit", t("ui.Session RPM Limit"))}
                        </div>

                        <div className="mt-6 flex justify-end gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => {
                              setAppliedDiscoveredSelection(null);
                              setIsEditing(false);
                              fetchAgentInfo();
                            }}
                          >
                            Cancel
                          </Button>
                          <Button type="submit" disabled={isSaving} aria-busy={isSaving}>
                            {isSaving && <UiLoadingSpinner className="size-4" />}
                            Save Changes
                          </Button>
                        </div>
                      </form>
                    </FormProvider>
                  </TooltipProvider>
                ) : (
                  <p>{t('ui.Click "Edit Settings" to modify agent configuration.')}</p>
                )}
              </Card>
            </TabsContent>
          )}
        </div>
      </Tabs>
    </div>
  );
};

export default AgentInfoView;
