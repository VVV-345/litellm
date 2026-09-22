import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { UserInfoV2Response, userGetInfoV2 } from "@/components/networking";
import { useQuery, UseQueryResult } from "@tanstack/react-query";
import { createQueryKeys } from "../common/queryKeysFactory";

export const userKeys = createQueryKeys("users");

export const currentUserQueryOptions = (accessToken: string | null, userId: string | null) => ({
  queryKey: userKeys.detail(userId ?? ""),
  queryFn: async () => {
    if (!accessToken) throw new Error("Access token required");
    return await userGetInfoV2(accessToken);
  },
  enabled: Boolean(accessToken && userId),
});

export const useCurrentUser = (): UseQueryResult<UserInfoV2Response> => {
  const { accessToken, userId } = useAuthorized();
  return useQuery<UserInfoV2Response>(currentUserQueryOptions(accessToken, userId));
};
