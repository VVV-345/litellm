import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Organization, organizationInfoCall, organizationListCall } from "@/components/networking";
import { useQuery, useQueryClient, UseQueryResult } from "@tanstack/react-query";
import { createQueryKeys } from "../common/queryKeysFactory";

export const organizationKeys = createQueryKeys("organizations");

export interface OrganizationListFilters {
  org_id?: string | null;
  org_alias?: string | null;
}

export const organizationQueryOptions = (
  accessToken: string | null,
  userId: string | null,
  userRole: string | null,
  filters?: OrganizationListFilters,
) => {
  const orgId = filters?.org_id || null;
  const orgAlias = filters?.org_alias || null;
  return {
    queryKey: organizationKeys.list(
      orgId || orgAlias
        ? { filters: { ...(orgId && { org_id: orgId }), ...(orgAlias && { org_alias: orgAlias }) } }
        : {},
    ),
    queryFn: async () => {
      if (!accessToken) throw new Error("Access token required");
      return await organizationListCall(accessToken, orgId, orgAlias);
    },
    enabled: Boolean(accessToken && userId && userRole),
  };
};

export const useOrganizations = (filters?: OrganizationListFilters): UseQueryResult<Organization[]> => {
  const { accessToken, userId, userRole } = useAuthorized();
  return useQuery<Organization[]>(organizationQueryOptions(accessToken, userId, userRole, filters));
};

export const useOrganization = (organizationID?: string) => {
  const queryClient = useQueryClient();
  const { accessToken } = useAuthorized();
  return useQuery<Organization>({
    queryKey: organizationKeys.detail(organizationID!),
    enabled: Boolean(accessToken && organizationID),

    queryFn: async () => {
      if (!accessToken || !organizationID) {
        throw new Error("Missing auth or teamId");
      }

      return organizationInfoCall(accessToken, organizationID);
    },

    initialData: () => {
      if (!organizationID) return undefined;

      return queryClient
        .getQueriesData<Organization[]>({ queryKey: organizationKeys.lists() })
        .flatMap(([, organizations]) => organizations ?? [])
        .find((organization) => organization.organization_id === organizationID);
    },
  });
};
