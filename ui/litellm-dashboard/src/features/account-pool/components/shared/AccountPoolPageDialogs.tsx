/** 本文件承接号池首页的创建、重新授权、测试、重置额度、配置与删除弹窗，控制首页组件体积。 */

"use client";

import dynamic from "next/dynamic";
import { useTranslation } from "react-i18next";

import ModuleLoading from "@/components/shared/ModuleLoading";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

import type { AutomationJob } from "../../api/AccountPoolAutomationApi";
import { loadAccountPoolCreateDialog, loadRuntimeConfigDialog } from "../../preloadModules";
import type {
  AccountPoolAuthorization,
  AccountPoolCreationSupplier,
  AccountPoolEnvironment,
} from "../../utils/AccountPoolTypes";

const AccountPoolAutomationPanel = dynamic(() =>
  import("../onboarding/AccountPoolAutomationPanel").then((module) => module.AccountPoolAutomationPanel),
);
const RuntimeConfigDialog = dynamic(loadRuntimeConfigDialog, { loading: ModuleLoading });
const AccountPoolCreateDialog = dynamic(loadAccountPoolCreateDialog, { loading: ModuleLoading });
const AccountPoolModelTestDialog = dynamic(
  () => import("../cards/AccountPoolModelTestDialog").then((module) => module.AccountPoolModelTestDialog),
  { loading: ModuleLoading },
);
const AccountPoolQuotaResetDialog = dynamic(() =>
  import("../cards/AccountPoolQuotaResetDialog").then((module) => module.AccountPoolQuotaResetDialog),
);
const AccountPoolCredentialDialog = dynamic(() =>
  import("../cards/AccountPoolCredentialDialog").then((module) => module.AccountPoolCredentialDialog),
);

interface AccountPoolPageDialogsProps {
  accessToken: string | null;
  environments: readonly AccountPoolEnvironment[];
  createOpen: boolean;
  onCreateOpenChange: (open: boolean) => void;
  authorization: AccountPoolAuthorization | null;
  createSupplier: AccountPoolCreationSupplier;
  reauthorizingJob: AutomationJob | null;
  onCloseReauthorizingJob: () => void;
  testEnvironment: AccountPoolEnvironment | undefined;
  onCloseTestEnvironment: () => void;
  resetEnvironment: AccountPoolEnvironment | null;
  onCloseResetEnvironment: () => void;
  refreshCredentialEnvironment: AccountPoolEnvironment | null;
  onCloseRefreshCredentialEnvironment: () => void;
  verifyAccessEnvironment: AccountPoolEnvironment | null;
  onCloseVerifyAccessEnvironment: () => void;
  configEnvironment: AccountPoolEnvironment | null;
  onCloseConfigEnvironment: () => void;
  deleteEnvironment: AccountPoolEnvironment | null;
  onCloseDeleteEnvironment: () => void;
  deleting: boolean;
  onConfirmDelete: (environment: AccountPoolEnvironment) => void;
  onRefetchEnvironments: () => void;
  onOpenCreatedCardAutomation: (environmentId: string) => void;
}

export function AccountPoolPageDialogs({
  accessToken,
  environments,
  createOpen,
  onCreateOpenChange,
  authorization,
  createSupplier,
  reauthorizingJob,
  onCloseReauthorizingJob,
  testEnvironment,
  onCloseTestEnvironment,
  resetEnvironment,
  onCloseResetEnvironment,
  refreshCredentialEnvironment,
  onCloseRefreshCredentialEnvironment,
  verifyAccessEnvironment,
  onCloseVerifyAccessEnvironment,
  configEnvironment,
  onCloseConfigEnvironment,
  deleteEnvironment,
  onCloseDeleteEnvironment,
  deleting,
  onConfirmDelete,
  onRefetchEnvironments,
  onOpenCreatedCardAutomation,
}: AccountPoolPageDialogsProps) {
  const { t } = useTranslation();

  return (
    <>
      {createOpen && (
        <AccountPoolCreateDialog
          key={authorization?.environment.id ?? "create"}
          accessToken={accessToken}
          initialAuthorization={authorization}
          initialSupplier={createSupplier}
          environments={environments}
          open
          onOpenChange={onCreateOpenChange}
          onCreated={onRefetchEnvironments}
          onAutomation={onOpenCreatedCardAutomation}
        />
      )}
      {reauthorizingJob && accessToken && (
        <Dialog open onOpenChange={(open) => !open && onCloseReauthorizingJob()}>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>{t("accountPool.mutation.automaticReauthorization")}</DialogTitle>
              <DialogDescription>{t("accountPool.mutation.automaticReauthorizationDescription")}</DialogDescription>
            </DialogHeader>
            <AccountPoolAutomationPanel
              key={reauthorizingJob.id}
              accessToken={accessToken}
              reauthorizingJob={reauthorizingJob}
            />
          </DialogContent>
        </Dialog>
      )}
      {testEnvironment && accessToken && (
        <AccountPoolModelTestDialog
          key={testEnvironment.id}
          accessToken={accessToken}
          environment={testEnvironment}
          onClose={onCloseTestEnvironment}
        />
      )}
      {resetEnvironment && accessToken && (
        <AccountPoolQuotaResetDialog
          key={resetEnvironment.id}
          accessToken={accessToken}
          environment={resetEnvironment}
          onClose={onCloseResetEnvironment}
        />
      )}
      {refreshCredentialEnvironment && accessToken && (
        <AccountPoolCredentialDialog
          key={refreshCredentialEnvironment.id}
          mode="refresh"
          accessToken={accessToken}
          environment={refreshCredentialEnvironment}
          onClose={onCloseRefreshCredentialEnvironment}
        />
      )}
      {verifyAccessEnvironment && accessToken && (
        <AccountPoolCredentialDialog
          key={verifyAccessEnvironment.id}
          mode="verify"
          accessToken={accessToken}
          environment={verifyAccessEnvironment}
          onClose={onCloseVerifyAccessEnvironment}
        />
      )}
      {configEnvironment && (
        <RuntimeConfigDialog
          key={configEnvironment.id}
          accessToken={accessToken}
          environment={configEnvironment}
          open
          onOpenChange={(open) => !open && onCloseConfigEnvironment()}
          onRefresh={onRefetchEnvironments}
          onSaved={() => {
            onCloseConfigEnvironment();
            onRefetchEnvironments();
          }}
        />
      )}

      <AlertDialog
        open={deleteEnvironment !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) onCloseDeleteEnvironment();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("accountPool.confirmDeleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteEnvironment
                ? t("accountPool.confirmDeleteDescription", { name: deleteEnvironment.name })
                : t("accountPool.confirmDeleteUnavailable")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t("accountPool.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleting || deleteEnvironment === null}
              onClick={() => {
                if (deleteEnvironment) onConfirmDelete(deleteEnvironment);
              }}
            >
              {deleting ? t("accountPool.deleting") : t("accountPool.confirmDelete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
