import type useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { formatUserRole, isViewOnlySessionRole } from "@/utils/roles";

type Authorization = ReturnType<typeof useAuthorized>;

export const authorizationFixture = (overrides: Partial<Authorization> = {}): Authorization => ({
  isLoading: false,
  isAuthorized: true,
  token: "test-token",
  accessToken: "test-token",
  userId: "test-user",
  userEmail: "test@example.com",
  userRole: "Admin",
  userRoleLabel: formatUserRole(overrides.userRole ?? "Admin"),
  isViewOnly: isViewOnlySessionRole(overrides.userRole ?? "Admin"),
  premiumUser: false,
  disabledPersonalKeyCreation: null,
  showSSOBanner: false,
  ...overrides,
});
