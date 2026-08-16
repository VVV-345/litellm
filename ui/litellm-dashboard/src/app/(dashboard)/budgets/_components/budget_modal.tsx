import React from "react";
import { useTranslation } from "react-i18next";
import { TextInput, Accordion, AccordionHeader, AccordionBody } from "@tremor/react";
import { Button as Button2, Modal, Form, InputNumber, Select } from "antd";
import { useCreateBudget } from "@/app/(dashboard)/hooks/budgets/useBudgets";
import NotificationsManager from "@/components/molecules/notifications_manager";

interface BudgetModalProps {
  isModalVisible: boolean;
  setIsModalVisible: React.Dispatch<React.SetStateAction<boolean>>;
}
const BudgetModal: React.FC<BudgetModalProps> = ({ isModalVisible, setIsModalVisible }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const createBudget = useCreateBudget();

  const handleOk = () => {
    setIsModalVisible(false);
    form.resetFields();
  };

  const handleCancel = () => {
    setIsModalVisible(false);
    form.resetFields();
  };

  const handleCreate = async (formValues: Record<string, any>) => {
    try {
      NotificationsManager.info(t("ui.Making API Call"));
      await createBudget.mutateAsync(formValues);
      NotificationsManager.success(t("ui.Budget Created"));
      form.resetFields();
      setIsModalVisible(false);
    } catch (error) {
      console.error("Error creating the budget:", error);
      NotificationsManager.fromBackend(`Error creating the budget: ${error}`);
    }
  };

  return (
    <Modal
      title={t("ui.Create Budget")}
      open={isModalVisible}
      width={800}
      footer={null}
      onOk={handleOk}
      onCancel={handleCancel}
    >
      <Form form={form} onFinish={handleCreate} labelCol={{ span: 8 }} wrapperCol={{ span: 16 }} labelAlign="left">
        <>
          <Form.Item
            label={t("ui.Budget ID")}
            name="budget_id"
            rules={[
              {
                required: true,
                message: t("ui.Please input a human-friendly name for the budget"),
              },
            ]}
            help={t("ui.A human-friendly name for the budget")}
          >
            <TextInput placeholder="" />
          </Form.Item>
          <Form.Item label={t("ui.Max Tokens per minute")} name="tpm_limit" help={t("ui.Default is model limit.")}>
            <InputNumber step={1} precision={2} width={200} />
          </Form.Item>
          <Form.Item label={t("ui.Max Requests per minute")} name="rpm_limit" help={t("ui.Default is model limit.")}>
            <InputNumber step={1} precision={2} width={200} />
          </Form.Item>

          <Accordion className="mt-20 mb-8">
            <AccordionHeader>
              <b>{t("ui.Optional Settings")}</b>
            </AccordionHeader>
            <AccordionBody>
              <Form.Item label={t("ui.Max Budget (USD)")} name="max_budget">
                <InputNumber step={0.01} precision={2} width={200} />
              </Form.Item>
              <Form.Item className="mt-8" label={t("ui.Reset Budget")} name="budget_duration">
                <Select defaultValue={null} placeholder={t("ui.n/a")}>
                  <Select.Option value="24h">{t("ui.daily")}</Select.Option>
                  <Select.Option value="7d">{t("ui.weekly")}</Select.Option>
                  <Select.Option value="30d">{t("ui.monthly")}</Select.Option>
                </Select>
              </Form.Item>
            </AccordionBody>
          </Accordion>
        </>

        <div style={{ textAlign: "right", marginTop: "10px" }}>
          <Button2 htmlType="submit">{t("ui.Create Budget")}</Button2>
        </div>
      </Form>
    </Modal>
  );
};

export default BudgetModal;
