import { $api } from "@/lib/http/api";
import { all_admin_roles } from "@/utils/roles";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import type { components } from "@/lib/http/schema";

export type EndUser = components["schemas"]["CustomerResponse"];

const customerHookOptions = (accessToken: string | null, userRole: string | null) => ({
  enabled: Boolean(accessToken) && all_admin_roles.includes(userRole!),
  select: (data: EndUser[] | undefined) => data ?? [],
});

export const customersQueryOptions = (accessToken: string | null, userRole: string | null) =>
  $api.queryOptions("get", "/customer/list", {}, customerHookOptions(accessToken, userRole));

export const useCustomers = () => {
  const { accessToken, userRole } = useAuthorized();
  return $api.useQuery("get", "/customer/list", {}, customerHookOptions(accessToken, userRole));
};
