"use client";

import { AccountKeyDialog } from "./AccountKeyDialog";
import { useAccountPoolQuery } from "@/app/(dashboard)/account-pool/useAccountPoolQuery";
import { canManageAccountPool } from "@/app/(dashboard)/account-pool/AccountPoolPermissions";
import { parseAsString, useQueryState } from "nuqs";
import { teamListCall as v2TeamListCall } from "@/app/(dashboard)/hooks/teams/useTeams";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { KeyResponse, Team } from "@/components/key_team_helpers/key_list";
import { CreateKeyPrefillData } from "@/components/organisms/create_key_button";
import UserDashboard from "@/components/user_dashboard";
import { useAuth } from "@/contexts/AuthContext";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

export default function ApiKeysDashboard() {
  // Identity comes from useAuthorized (synchronous cookie decode) so userID is set whenever the
  // route is authorized; useAuth only supplies the backfill setters UserDashboard still expects.
  const { userId: userID, userRole, userEmail, accessToken, premiumUser } = useAuthorized();
  const { setUserRole, setUserEmail } = useAuth();
  const searchParams = useSearchParams()!;
  const [accountId, setAccountId] = useQueryState("account_id", parseAsString);
  const canManageAccounts = canManageAccountPool(userRole, false);
  const accounts = useAccountPoolQuery(accessToken, canManageAccounts, false);

  const [teams, setTeams] = useState<Team[] | null>(null);
  const [keys, setKeys] = useState<KeyResponse[] | null>([]);
  const [createClicked, setCreateClicked] = useState<boolean>(false);

  const autoOpenCreate = searchParams.get("create") === "true";
  const prefillData: CreateKeyPrefillData | undefined = useMemo(() => {
    if (!autoOpenCreate) return undefined;

    const ownedBy = searchParams.get("owned_by");
    const teamId = searchParams.get("team_id");
    const keyAlias = searchParams.get("key_alias");
    const modelsParam = searchParams.get("models");
    const keyType = searchParams.get("key_type");

    if (!ownedBy && !teamId && !keyAlias && !modelsParam && !keyType) {
      return undefined;
    }

    const validOwnedByValues = ["you", "service_account", "another_user"];
    const validatedOwnedBy =
      ownedBy && validOwnedByValues.includes(ownedBy) ? (ownedBy as CreateKeyPrefillData["owned_by"]) : undefined;

    const validKeyTypes = ["default", "llm_api", "management"];
    const validatedKeyType =
      keyType && validKeyTypes.includes(keyType) ? (keyType as CreateKeyPrefillData["key_type"]) : undefined;

    const sanitizedKeyAlias = keyAlias ? keyAlias.trim().slice(0, 256) : undefined;

    const sanitizedModels = modelsParam
      ? modelsParam
          .split(",")
          .slice(0, 100)
          .map((m) => m.trim().slice(0, 256))
          .filter((m) => m.length > 0)
      : undefined;

    return {
      owned_by: validatedOwnedBy,
      team_id: teamId?.trim() || undefined,
      key_alias: sanitizedKeyAlias,
      models: sanitizedModels && sanitizedModels.length > 0 ? sanitizedModels : undefined,
      key_type: validatedKeyType,
    };
  }, [searchParams, autoOpenCreate]);

  const addKey = (data: KeyResponse) => {
    setKeys((prevData) => (prevData ? [...prevData, data] : [data]));
    setCreateClicked((prev) => !prev);
  };

  useEffect(() => {
    if (accessToken && userID && userRole) {
      v2TeamListCall(accessToken, 1, 100, {
        userID: userRole !== "Admin" && userRole !== "Admin Viewer" ? userID : null,
      })
        .then((response) => setTeams(response.teams ?? []))
        .catch(console.error);
    }
  }, [accessToken, userID, userRole]);

  return (
    <>
      {canManageAccounts && (
        <div className="mx-6 mt-4 flex flex-wrap items-center gap-3 rounded-lg border p-4">
          <label htmlFor="key-account">账号专用密钥</label>
          <select
            id="key-account"
            className="rounded-md border bg-background px-3 py-2"
            value={accountId ?? ""}
            onChange={(event) => void setAccountId(event.target.value || null)}
          >
            <option value="">选择账号以管理专用密钥</option>
            {accountId && !accounts.data?.some((account) => account.id === accountId) && (
              <option value={accountId}>{accountId}</option>
            )}
            {accounts.data?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
          {accounts.isError && <p role="alert">账号列表读取失败，请刷新重试</p>}
        </div>
      )}
      {canManageAccounts && accountId && accessToken && (
        <AccountKeyDialog
          key={accountId}
          accessToken={accessToken}
          cardId={accountId}
          name={accounts.data?.find((account) => account.id === accountId)?.name ?? accountId}
          onClose={() => void setAccountId(null)}
        />
      )}
      <UserDashboard
        userID={userID}
        userRole={userRole}
        premiumUser={premiumUser ?? false}
        teams={teams}
        keys={keys}
        setUserRole={setUserRole}
        userEmail={userEmail}
        setUserEmail={setUserEmail}
        setTeams={setTeams}
        setKeys={setKeys}
        addKey={addKey}
        createClicked={createClicked}
        autoOpenCreate={autoOpenCreate}
        prefillData={prefillData}
      />
    </>
  );
}
