/**
 * The parent pane, showing list of budgets
 *
 */

import { Plus, Wallet } from "lucide-react";
import React, { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { PageHeader } from "@/components/shared/PageHeader";
import { ToolbarSeparator } from "@/components/shared/ToolbarSeparator";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import DeleteResourceModal from "@/components/common_components/DeleteResourceModal";
import NotificationsManager from "@/components/molecules/notifications_manager";
import { useBudgetList, useDeleteBudget, budgetItem } from "@/app/(dashboard)/hooks/budgets/useBudgets";
import BudgetModal from "./budget_modal";
import BudgetTable from "./BudgetTable";
import EditBudgetModal from "./edit_budget_modal";
import { CREATE_END_USER_CURL_COMMAND, CHAT_COMPLETIONS_CURL_COMMAND, OPENAI_SDK_PYTHON_CODE } from "./constants";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { isProxyAdminRole } from "@/utils/roles";

interface BudgetSettingsPageProps {
  accessToken: string | null;
}

const BudgetPanel: React.FC<BudgetSettingsPageProps> = ({ accessToken }) => {
  const { t } = useTranslation();
  const [isCreateModelVisible, setIsCreateModelVisible] = useState(false);
  const [isEditModalVisible, setIsEditModalVisible] = useState(false);
  const [selectedBudget, setSelectedBudget] = useState<budgetItem | null>(null);
  const [isDeleteModalVisible, setIsDeleteModalVisible] = useState(false);

  const { userRole } = useAuthorized();
  // Admin Viewer follows the read-parity rule: see budgets, no writes.
  const canModify = isProxyAdminRole(userRole ?? "");

  const budgetList = useBudgetList();
  const deleteBudget = useDeleteBudget();

  // Stable identities keep the memoized column defs stable; new ones remount every header and cell.
  const handleEditCall = useCallback(
    (budget: budgetItem) => {
      if (accessToken == null) {
        return;
      }
      setSelectedBudget(budget);
      setIsEditModalVisible(true);
    },
    [accessToken],
  );

  const handleDeleteClick = useCallback((budget: budgetItem) => {
    setSelectedBudget(budget);
    setIsDeleteModalVisible(true);
  }, []);

  const handleDeleteConfirm = async () => {
    if (!selectedBudget || accessToken == null) {
      return;
    }
    try {
      await deleteBudget.mutateAsync(selectedBudget.budget_id);
      NotificationsManager.success(t("ui.Budget deleted."));
    } catch (error) {
      console.error("Error deleting budget:", error);
      if (typeof NotificationsManager.fromBackend === "function") {
        NotificationsManager.fromBackend(t("ui.Failed to delete budget"));
      } else {
        NotificationsManager.info(t("ui.Failed to delete budget"));
      }
    } finally {
      setIsDeleteModalVisible(false);
      setSelectedBudget(null);
    }
  };

  const handleDeleteCancel = () => {
    setIsDeleteModalVisible(false);
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6 px-12">
      <PageHeader
        icon={<Wallet className="size-5" />}
        title={t("ui.Budgets")}
        subtitle={t("ui.Spend, TPM and RPM limits you can assign to customers.")}
      />
      <Tabs defaultValue="budgets" className="min-h-0 flex-1 gap-0">
        <div className="flex items-center gap-4 border-b border-border">
          {canModify && (
            <>
              <Button onClick={() => setIsCreateModelVisible(true)}>
                <Plus className="size-4" />
                {t("ui.Create Budget")}
              </Button>
              <ToolbarSeparator className="h-6" />
            </>
          )}
          <TabsList variant="line">
            <TabsTrigger value="budgets" className="flex-none px-4">
              {t("ui.Budgets")}
            </TabsTrigger>
            <TabsTrigger value="examples" className="flex-none px-4">
              {t("ui.Examples")}
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="budgets" className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col pt-6">
            <BudgetModal isModalVisible={isCreateModelVisible} setIsModalVisible={setIsCreateModelVisible} />
            {selectedBudget && (
              <EditBudgetModal
                isModalVisible={isEditModalVisible}
                setIsModalVisible={setIsEditModalVisible}
                existingBudget={selectedBudget}
              />
            )}
            <BudgetTable
              list={budgetList}
              canModify={canModify}
              onEditClick={handleEditCall}
              onDeleteClick={handleDeleteClick}
            />
            <DeleteResourceModal
              isOpen={isDeleteModalVisible}
              title={t("ui.Delete Budget?")}
              message={t("ui.Are you sure you want to delete this budget? This action cannot be undone.")}
              resourceInformationTitle={t("ui.Budget Information")}
              resourceInformation={[
                { label: t("ui.Budget ID"), value: selectedBudget?.budget_id, code: true },
                { label: t("ui.Max Budget"), value: selectedBudget?.max_budget },
                { label: t("ui.TPM"), value: selectedBudget?.tpm_limit },
                { label: t("ui.RPM"), value: selectedBudget?.rpm_limit },
              ]}
              onCancel={handleDeleteCancel}
              onOk={handleDeleteConfirm}
              confirmLoading={deleteBudget.isPending}
            />
          </div>
        </TabsContent>
        <TabsContent value="examples" className="min-h-0 flex-1 overflow-y-auto">
          <div className="pt-6">
            <p className="text-base text-muted-foreground">{t("ui.How to use budget id")}</p>
            <Tabs defaultValue="assign-budget">
              <TabsList variant="line" className="h-auto w-full justify-start rounded-none border-b p-0">
                <TabsTrigger value="assign-budget" className="flex-none rounded-none px-4 py-2">
                  {t("ui.Assign Budget to Customer")}
                </TabsTrigger>
                <TabsTrigger value="curl" className="flex-none rounded-none px-4 py-2">
                  {t("ui.Test it (Curl)")}
                </TabsTrigger>
                <TabsTrigger value="openai-sdk" className="flex-none rounded-none px-4 py-2">
                  {t("ui.Test it (OpenAI SDK)")}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="assign-budget">
                <SyntaxHighlighter language="bash">{CREATE_END_USER_CURL_COMMAND}</SyntaxHighlighter>
              </TabsContent>
              <TabsContent value="curl">
                <SyntaxHighlighter language="bash">{CHAT_COMPLETIONS_CURL_COMMAND}</SyntaxHighlighter>
              </TabsContent>
              <TabsContent value="openai-sdk">
                <SyntaxHighlighter language="python">{OPENAI_SDK_PYTHON_CODE}</SyntaxHighlighter>
              </TabsContent>
            </Tabs>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default BudgetPanel;
