import { useTeams } from "@/app/(dashboard)/hooks/teams/useTeams";
import { organizationKeys, useOrganization } from "@/app/(dashboard)/hooks/organizations/useOrganizations";
import { useQueryClient } from "@tanstack/react-query";
import { useVisitedTabs } from "@/hooks/useVisitedTabs";
import { MoneyCell } from "@/components/shared/table_cells";
import CopyButton from "@/components/shared/CopyButton";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatNumberWithCommas } from "@/utils/dataUtils";
import { teamDetailHref } from "@/utils/entityLinks";
import { createTeamAliasMap } from "@/utils/teamUtils";
import { BadgeLink } from "@/components/shared/BadgeLink";
import { ArrowLeft } from "lucide-react";
import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import MemberTable, { type MemberTableColumn } from "../common_components/MemberTable";
import UserSearchModal from "../common_components/user_search_modal";
import { toast } from "@/lib/toast";
import {
  Member,
  organizationMemberAddCall,
  organizationMemberDeleteCall,
  organizationMemberUpdateCall,
} from "../networking";
import ObjectPermissionsView from "../object_permissions_view";
import MemberModal from "../team/EditMembership";
import { OrgSettingsForm } from "./org-settings/OrgSettingsForm";

interface OrganizationInfoProps {
  organizationId: string;
  onClose: () => void;
  accessToken: string | null;
  is_org_admin: boolean;
  is_proxy_admin: boolean;
  userModels: string[];
  editOrg: boolean;
}

const OrganizationInfoView: React.FC<OrganizationInfoProps> = ({
  organizationId,
  onClose,
  accessToken,
  is_org_admin,
  is_proxy_admin,
  userModels,
  editOrg,
}) => {
  const { t } = useTranslation();
  const ui = (value: string) => t(`ui.${value}`, { defaultValue: value });
  const queryClient = useQueryClient();
  const { data: orgData, isLoading: loading } = useOrganization(organizationId);
  const [isEditing, setIsEditing] = useState(false);
  const [isAddMemberModalVisible, setIsAddMemberModalVisible] = useState(false);
  const [isEditMemberModalVisible, setIsEditMemberModalVisible] = useState(false);
  const [selectedEditMember, setSelectedEditMember] = useState<Member | null>(null);
  const canEditOrg = is_org_admin || is_proxy_admin;
  const { data: teams } = useTeams();
  const { onTabChange, hasVisited } = useVisitedTabs(editOrg ? "settings" : "overview");

  const teamAliasMap = useMemo(() => createTeamAliasMap(teams), [teams]);

  const handleMemberAdd = async (values: any) => {
    try {
      if (accessToken == null) {
        return;
      }

      const member: Member = {
        user_email: values.user_email,
        user_id: values.user_id,
        role: values.role,
      };
      await organizationMemberAddCall(accessToken, organizationId, member);

      toast.success(ui("Organization member added successfully"));
      setIsAddMemberModalVisible(false);
      queryClient.invalidateQueries({ queryKey: organizationKeys.all });
    } catch (error) {
      toast.fromError(ui("Failed to add organization member"));
      console.error("Error adding organization member:", error);
    }
  };

  const handleMemberUpdate = async (values: any) => {
    try {
      if (!accessToken) return;

      const member: Member = {
        user_email: values.user_email,
        user_id: values.user_id,
        role: values.role,
      };

      await organizationMemberUpdateCall(accessToken, organizationId, member);
      toast.success(ui("Organization member updated successfully"));
      setIsEditMemberModalVisible(false);
      queryClient.invalidateQueries({ queryKey: organizationKeys.all });
    } catch (error) {
      toast.fromError(ui("Failed to update organization member"));
      console.error("Error updating organization member:", error);
    }
  };

  const handleMemberDelete = async (values: any) => {
    try {
      if (!accessToken) return;

      await organizationMemberDeleteCall(accessToken, organizationId, values.user_id);
      toast.success(ui("Organization member deleted successfully"));
      setIsEditMemberModalVisible(false);
      queryClient.invalidateQueries({ queryKey: organizationKeys.all });
    } catch (error) {
      toast.fromError(ui("Failed to delete organization member"));
      console.error("Error deleting organization member:", error);
    }
  };

  if (loading) {
    return <div className="p-4">{ui("Loading...")}</div>;
  }

  if (!orgData) {
    return <div className="p-4">{ui("Organization not found")}</div>;
  }

  const orgExtraColumns: MemberTableColumn[] = [
    {
      title: ui("Spend (USD)"),
      key: "spend",
      render: (_: unknown, record: Member) => {
        const orgMember =
          record.user_id != null ? (orgData.members || []).find((m) => m.user_id === record.user_id) : undefined;
        return <MoneyCell value={orgMember?.spend} decimals={4} />;
      },
    },
    {
      title: ui("Created At"),
      key: "created_at",
      render: (_: unknown, record: Member) => {
        const orgMember =
          record.user_id != null ? (orgData.members || []).find((m) => m.user_id === record.user_id) : undefined;
        return <span>{orgMember?.created_at ? new Date(orgMember.created_at).toLocaleString() : "-"}</span>;
      },
    },
  ];

  return (
    <div className="h-screen w-full bg-background p-4">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Button variant="ghost" onClick={onClose} className="mb-4">
            <ArrowLeft className="size-4" />
            {ui("Back to Organizations")}
          </Button>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">{orgData.organization_alias}</h1>
          <div className="flex items-center gap-1">
            <span className="font-mono text-sm text-muted-foreground">{orgData.organization_id}</span>
            <CopyButton value={orgData.organization_id} label={ui("Copy organization ID")} iconClassName="size-3" />
          </div>
        </div>
      </div>

      <Tabs defaultValue={editOrg ? "settings" : "overview"} onValueChange={onTabChange} className="mb-4">
        <TabsList variant="line" className="h-auto w-full justify-start rounded-none border-b p-0">
          <TabsTrigger value="overview" className="flex-none rounded-none px-4 py-2">
            {ui("Overview")}
          </TabsTrigger>
          <TabsTrigger value="members" className="flex-none rounded-none px-4 py-2">
            {ui("Members")}
          </TabsTrigger>
          <TabsTrigger value="settings" className="flex-none rounded-none px-4 py-2">
            {ui("Settings")}
          </TabsTrigger>
        </TabsList>

        <TabsContent keepMounted={hasVisited("overview")} value="overview" className="pt-4">
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <Card>
              <CardContent>
                <p className="text-sm text-muted-foreground">{ui("Organization Details")}</p>
                <div className="mt-2 text-sm text-foreground">
                  <p>{ui("Created:")} {new Date(orgData.created_at).toLocaleDateString()}</p>
                  <p>{ui("Updated:")} {new Date(orgData.updated_at).toLocaleDateString()}</p>
                  <p>{ui("Created By:")} {orgData.created_by}</p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <p className="text-sm text-muted-foreground">{ui("Budget Status")}</p>
                <div className="mt-2 text-sm text-foreground">
                  <p className="text-xl font-semibold">${formatNumberWithCommas(orgData.spend, 4)}</p>
                  <p>
                    {ui("of")}{" "}
                    {orgData.litellm_budget_table.max_budget === null
                      ? ui("Unlimited")
                      : `$${formatNumberWithCommas(orgData.litellm_budget_table.max_budget, 4)}`}
                  </p>
                  {orgData.litellm_budget_table.budget_duration && (
                    <p className="text-muted-foreground">{ui("Reset:")} {orgData.litellm_budget_table.budget_duration}</p>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <p className="text-sm text-muted-foreground">{ui("Rate Limits")}</p>
                <div className="mt-2 text-sm text-foreground">
                  <p>{ui("TPM:")} {orgData.litellm_budget_table.tpm_limit ?? ui("Unlimited")}</p>
                  <p>{ui("RPM:")} {orgData.litellm_budget_table.rpm_limit ?? ui("Unlimited")}</p>
                  {orgData.litellm_budget_table.max_parallel_requests && (
                    <p>{ui("Max Parallel Requests:")} {orgData.litellm_budget_table.max_parallel_requests}</p>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <p className="text-sm text-muted-foreground">{ui("Models")}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {orgData.models.length === 0 ? (
                    <BadgeLink>{ui("All proxy models")}</BadgeLink>
                  ) : (
                    orgData.models.map((model, index) => <BadgeLink key={index}>{model}</BadgeLink>)
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent>
                <p className="text-sm text-muted-foreground">{ui("Teams")}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {orgData.teams?.map((team, index) => (
                    <BadgeLink key={index} href={teamDetailHref(team.team_id)}>
                      {teamAliasMap[team.team_id] || team.team_id}
                    </BadgeLink>
                  ))}
                </div>
              </CardContent>
            </Card>

            <ObjectPermissionsView
              objectPermission={orgData.object_permission}
              variant="card"
              accessToken={accessToken}
            />
          </div>
        </TabsContent>

        <TabsContent keepMounted={hasVisited("members")} value="members" className="pt-4">
          <div className="space-y-4">
            <MemberTable
              members={(orgData.members || []).map((m) => ({
                role: m.user_role || "",
                user_id: m.user_id,
                user_email: m.user_email,
              }))}
              canEdit={canEditOrg}
              onEdit={(member) => {
                setSelectedEditMember(member);
                setIsEditMemberModalVisible(true);
              }}
              onDelete={(member) => handleMemberDelete(member)}
              onAddMember={() => setIsAddMemberModalVisible(true)}
              roleColumnTitle={ui("Organization Role")}
              extraColumns={orgExtraColumns}
              emptyText={ui("No members found")}
            />
          </div>
        </TabsContent>

        <TabsContent keepMounted={hasVisited("settings")} value="settings" className="pt-4">
          <Card className="max-h-[65vh] overflow-y-auto">
            <CardContent>
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-foreground">{ui("Organization Settings")}</h2>
                {canEditOrg && !isEditing && <Button onClick={() => setIsEditing(true)}>{ui("Edit Settings")}</Button>}
              </div>

              {isEditing ? (
                <OrgSettingsForm
                  organizationId={organizationId}
                  org={orgData}
                  accessToken={accessToken || ""}
                  onCancel={() => setIsEditing(false)}
                  onSaved={() => setIsEditing(false)}
                />
              ) : (
                <div className="space-y-4 text-sm">
                  <div>
                    <p className="font-medium text-foreground">{ui("Organization Name")}</p>
                    <div>{orgData.organization_alias}</div>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{ui("Organization ID")}</p>
                    <div className="font-mono">{orgData.organization_id}</div>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{ui("Created At")}</p>
                    <div>{new Date(orgData.created_at).toLocaleString()}</div>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{ui("Models")}</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {orgData.models.map((model, index) => (
                        <BadgeLink key={index}>{model}</BadgeLink>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{ui("Rate Limits")}</p>
                    <div>{ui("TPM:")} {orgData.litellm_budget_table.tpm_limit ?? ui("Unlimited")}</div>
                    <div>{ui("RPM:")} {orgData.litellm_budget_table.rpm_limit ?? ui("Unlimited")}</div>
                  </div>
                  <div>
                    <p className="font-medium text-foreground">{ui("Budget")}</p>
                    <div>
                      {ui("Max:")}{" "}
                      {orgData.litellm_budget_table.max_budget !== null
                        ? `$${formatNumberWithCommas(orgData.litellm_budget_table.max_budget, 4)}`
                        : ui("No Limit")}
                    </div>
                    <div>{ui("Reset:")} {orgData.litellm_budget_table.budget_duration || ui("Never")}</div>
                  </div>

                  <ObjectPermissionsView
                    objectPermission={orgData.object_permission}
                    variant="inline"
                    className="border-t pt-4"
                    accessToken={accessToken}
                  />
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <UserSearchModal
        isVisible={isAddMemberModalVisible}
        onCancel={() => setIsAddMemberModalVisible(false)}
        onSubmit={handleMemberAdd}
        accessToken={accessToken}
        title={ui("Add Organization Member")}
        roles={[
          {
            label: "org_admin",
            value: "org_admin",
            description: ui("Can add and remove members, and change their roles."),
          },
          {
            label: "internal_user",
            value: "internal_user",
            description: ui("Can view/create keys for themselves within organization."),
          },
          {
            label: "internal_user_viewer",
            value: "internal_user_viewer",
            description: ui("Can only view their keys within organization."),
          },
        ]}
        defaultRole="internal_user"
      />
      <MemberModal
        visible={isEditMemberModalVisible}
        onCancel={() => setIsEditMemberModalVisible(false)}
        onSubmit={handleMemberUpdate}
        initialData={selectedEditMember}
        mode="edit"
        config={{
          title: ui("Edit Member"),
          showEmail: true,
          showUserId: true,
          roleOptions: [
            { label: ui("Org Admin"), value: "org_admin" },
            { label: ui("Internal User"), value: "internal_user" },
            { label: ui("Internal User Viewer"), value: "internal_user_viewer" },
          ],
        }}
      />
    </div>
  );
};

export default OrganizationInfoView;
