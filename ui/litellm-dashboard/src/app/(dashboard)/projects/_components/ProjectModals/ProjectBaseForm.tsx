import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Col,
  Collapse,
  Divider,
  Flex,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Typography,
  Button,
} from "antd";
import type { FormInstance } from "antd";
import { PlusOutlined, MinusCircleOutlined } from "@ant-design/icons";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { useTeams } from "@/app/(dashboard)/hooks/teams/useTeams";
import { Team } from "@/components/key_team_helpers/key_list";
import { fetchTeamModels } from "@/components/organisms/create_key_button";
import { getModelDisplayName } from "@/components/key_team_helpers/fetch_available_models_team_key";
import { getGuardrailsList } from "@/components/networking";

export interface ProjectFormValues {
  project_alias: string;
  team_id: string;
  description?: string;
  models: string[];
  max_budget?: number;
  isBlocked: boolean;
  guardrails?: string[];
  modelLimits?: { model: string; tpm?: number; rpm?: number }[];
  metadata?: { key: string; value: string }[];
}

interface ProjectBaseFormProps {
  form: FormInstance<ProjectFormValues>;
}

export function ProjectBaseForm({ form }: ProjectBaseFormProps) {
  const { t } = useTranslation();
  const { accessToken, userId, userRole } = useAuthorized();
  const { data: teams } = useTeams();

  const [selectedTeam, setSelectedTeam] = useState<Team | null>(null);
  const [modelsToPick, setModelsToPick] = useState<string[]>([]);
  const [guardrailsList, setGuardrailsList] = useState<string[]>([]);

  useEffect(() => {
    const fetchGuardrails = async () => {
      if (!accessToken) return;
      try {
        const response = await getGuardrailsList(accessToken);
        const names = response.guardrails.map((g: { guardrail_name: string }) => g.guardrail_name);
        setGuardrailsList(names);
      } catch (error) {
        console.error("Failed to fetch guardrails:", error);
      }
    };
    fetchGuardrails();
  }, [accessToken]);

  // Sync selectedTeam from form value (needed for edit mode pre-fill)
  const teamIdValue = Form.useWatch("team_id", form);
  useEffect(() => {
    if (teamIdValue && teams) {
      const team = teams.find((t) => t.team_id === teamIdValue) ?? null;
      if (team && team.team_id !== selectedTeam?.team_id) {
        setSelectedTeam(team);
      }
    }
  }, [teamIdValue, teams, selectedTeam?.team_id]);

  // Fetch team-scoped models when team selection changes
  useEffect(() => {
    if (userId && userRole && accessToken && selectedTeam) {
      fetchTeamModels(userId, userRole, accessToken, selectedTeam.team_id).then((models) => {
        const allModels = Array.from(new Set([...(selectedTeam.models ?? []), ...models]));
        setModelsToPick(allModels);
      });
    } else {
      setModelsToPick([]);
    }
  }, [selectedTeam, accessToken, userId, userRole]);

  const handleTeamChange = (teamId: string) => {
    const team = teams?.find((t) => t.team_id === teamId) ?? null;
    setSelectedTeam(team);
    form.setFieldValue("models", []);
  };

  return (
    <Form
      form={form}
      layout="vertical"
      name="project_form"
      initialValues={{ isBlocked: false }}
      style={{ marginTop: 24 }}
    >
      {/* Basic Info */}
      <Typography.Text
        strong
        style={{
          fontSize: 13,
          color: "#374151",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
        }}
      >
        {t("ui.Basic Information")}
      </Typography.Text>
      <Divider style={{ marginTop: 8, marginBottom: 16 }} />

      <Row gutter={24}>
        <Col span={12}>
          <Form.Item
            name="project_alias"
            label={t("ui.Project Name")}
            rules={[{ required: true, message: t("ui.Please enter a project name") }]}
          >
            <Input placeholder={t("ui.e.g. Customer Support Bot")} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="team_id" label={t("ui.Team")} rules={[{ required: true, message: t("ui.Please select a team") }]}>
            <Select
              showSearch
              placeholder={t("ui.Search or select a team")}
              onChange={handleTeamChange}
              allowClear
              optionLabelProp="label"
              filterOption={(input, option) => {
                const team = teams?.find((t) => t.team_id === option?.value);
                if (!team) return false;
                const search = input.toLowerCase().trim();
                return (
                  (team.team_alias || "").toLowerCase().includes(search) || team.team_id.toLowerCase().includes(search)
                );
              }}
            >
              {teams?.map((team) => (
                <Select.Option key={team.team_id} value={team.team_id} label={team.team_alias || team.team_id}>
                  <span style={{ fontWeight: 500 }}>{team.team_alias}</span>{" "}
                  <span style={{ color: "#9ca3af" }}>({team.team_id})</span>
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Col>
      </Row>

      <Row>
        <Col span={24}>
          <Form.Item name="description" label={t("ui.Description")}>
            <Input.TextArea placeholder={t("ui.Describe the purpose of this project")} rows={3} />
          </Form.Item>
        </Col>
      </Row>

      <Row>
        <Col span={24}>
          <Form.Item
            name="models"
            label={t("ui.Allowed Models (scoped to selected team's models)")}
            help={!selectedTeam ? t("ui.Select a team first to see available models") : undefined}
          >
            <Select
              mode="multiple"
              placeholder={selectedTeam ? t("ui.Select models") : t("ui.Select a team first")}
              disabled={!selectedTeam}
              allowClear
              maxTagCount="responsive"
              onChange={(values) => {
                if (values.includes("all-team-models")) {
                  form.setFieldsValue({ models: ["all-team-models"] });
                }
              }}
            >
              <Select.Option key="all-team-models" value="all-team-models">
                {t("ui.All Team Models")}
              </Select.Option>
              {modelsToPick.map((model) => (
                <Select.Option key={model} value={model}>
                  {getModelDisplayName(model)}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={24}>
        <Col span={12}>
          <Form.Item name="max_budget" label={t("ui.Max Budget (USD)")}>
            <InputNumber prefix="$" style={{ width: "100%" }} placeholder="0.00" min={0} precision={2} />
          </Form.Item>
        </Col>
      </Row>

      {/* Advanced Settings */}
      <Row>
        <Col span={24}>
          <Collapse
            ghost
            style={{
              background: "#f9fafb",
              borderRadius: 8,
              border: "1px solid #e5e7eb",
            }}
            items={[
              {
                key: "1",
                label: (
                  <Typography.Text strong style={{ color: "#374151" }}>
                    {t("ui.Advanced Settings")}
                  </Typography.Text>
                ),
                children: (
                  <>
                    <Flex align="center" gap={12}>
                      <Typography.Text strong>{t("ui.Block Project")}</Typography.Text>
                      <Form.Item name="isBlocked" valuePropName="checked" noStyle>
                        <Switch />
                      </Form.Item>
                    </Flex>
                    <Form.Item noStyle shouldUpdate={(prev, cur) => prev.isBlocked !== cur.isBlocked}>
                      {({ getFieldValue }) =>
                        getFieldValue("isBlocked") ? (
                          <Alert
                            banner
                            type="warning"
                            showIcon
                            message={t("ui.All API requests using keys under this project will be rejected.")}
                            style={{ marginTop: 12 }}
                          />
                        ) : null
                      }
                    </Form.Item>

                    <Divider />

                    <Form.Item label={t("ui.Guardrails")} name="guardrails" help={t("ui.Select existing guardrails or enter new ones")}>
                      <Select
                        mode="tags"
                        style={{ width: "100%" }}
                        placeholder={t("ui.Select or enter guardrails")}
                        options={guardrailsList.map((name) => ({
                          value: name,
                          label: name,
                        }))}
                      />
                    </Form.Item>

                    <Divider />

                    <Typography.Text strong style={{ display: "block", marginBottom: 12 }}>
                      {t("ui.Model-Specific Limits")}
                    </Typography.Text>
                    <Form.List name="modelLimits">
                      {(fields, { add, remove }) => (
                        <>
                          {fields.map(({ key, name, ...restField }) => (
                            <Space key={key} style={{ display: "flex", marginBottom: 8 }} align="baseline">
                              <Form.Item
                                {...restField}
                                name={[name, "model"]}
                                rules={[
                                  { required: true, message: t("ui.Missing model") },
                                  {
                                    validator: (_, value) => {
                                      if (!value) return Promise.resolve();
                                      const all = form.getFieldValue("modelLimits") ?? [];
                                      const dupes = all.filter((entry: { model?: string }) => entry?.model === value);
                                      if (dupes.length > 1) {
                                        return Promise.reject(new Error(t("ui.Duplicate model")));
                                      }
                                      return Promise.resolve();
                                    },
                                  },
                                ]}
                              >
                                <Input placeholder={t("ui.Model name (e.g. gpt-4)")} />
                              </Form.Item>
                              <Form.Item {...restField} name={[name, "tpm"]}>
                                <InputNumber placeholder={t("ui.TPM Limit")} min={0} />
                              </Form.Item>
                              <Form.Item {...restField} name={[name, "rpm"]}>
                                <InputNumber placeholder={t("ui.RPM Limit")} min={0} />
                              </Form.Item>
                              <MinusCircleOutlined onClick={() => remove(name)} style={{ color: "#ef4444" }} />
                            </Space>
                          ))}
                          <Form.Item>
                            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
                              {t("ui.Add Model Limit")}
                            </Button>
                          </Form.Item>
                        </>
                      )}
                    </Form.List>

                    <Divider />

                    <Typography.Text strong style={{ display: "block", marginBottom: 12 }}>
                      {t("ui.Metadata")}
                    </Typography.Text>
                    <Form.List name="metadata">
                      {(fields, { add, remove }) => (
                        <>
                          {fields.map(({ key, name, ...restField }) => (
                            <Space key={key} style={{ display: "flex", marginBottom: 8 }} align="baseline">
                              <Form.Item
                                {...restField}
                                name={[name, "key"]}
                                rules={[
                                  { required: true, message: t("ui.Missing key") },
                                  {
                                    validator: (_, value) => {
                                      if (!value) return Promise.resolve();
                                      const all = form.getFieldValue("metadata") ?? [];
                                      const dupes = all.filter((entry: { key?: string }) => entry?.key === value);
                                      if (dupes.length > 1) {
                                        return Promise.reject(new Error(t("ui.Duplicate key")));
                                      }
                                      return Promise.resolve();
                                    },
                                  },
                                ]}
                              >
                                <Input placeholder={t("ui.Key")} />
                              </Form.Item>
                              <Form.Item
                                {...restField}
                                name={[name, "value"]}
                                rules={[{ required: true, message: t("ui.Missing value") }]}
                              >
                                <Input placeholder={t("ui.Value")} />
                              </Form.Item>
                              <MinusCircleOutlined onClick={() => remove(name)} style={{ color: "#ef4444" }} />
                            </Space>
                          ))}
                          <Form.Item>
                            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
                              {t("ui.Add Key-Value Pair")}
                            </Button>
                          </Form.Item>
                        </>
                      )}
                    </Form.List>
                  </>
                ),
              },
            ]}
          />
        </Col>
      </Row>
    </Form>
  );
}
