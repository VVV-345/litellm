import { InfoCircleOutlined } from "@ant-design/icons";
import { Select as AntdSelect, Card, InputNumber, Radio, Space, Switch, Tooltip, Typography } from "antd";
import React from "react";
import { useTranslation } from "react-i18next";
import ClassifierPromptEditor from "./ClassifierPromptEditor";
import {
  ClassifierFallback,
  ClassifierType,
  ComplexityRouterConfigValue,
  DEFAULT_CLASSIFIER_CONTEXT_PER_TURN_CHARS,
  DEFAULT_CLASSIFIER_CONTEXT_WINDOW_SIZE,
  DEFAULT_CLASSIFIER_FALLBACK,
  DEFAULT_CLASSIFIER_TIMEOUT_MS,
  DEFAULT_CLASSIFICATION_RUBRIC,
  NEW_CLASSIFIER_CLASSIFICATION_RUBRIC,
  CLASSIFICATION_RUBRIC_DESCRIPTIONS,
  CLASSIFICATION_RUBRIC_KEYS,
  ClassificationRubric,
  effectiveTierLabel,
} from "./ComplexityRouterConfig";

const { Text } = Typography;

const DEFAULT_SCORING_EXPLANATION =
  "The router scores each request across 7 dimensions: token count, code presence, reasoning markers, technical " +
  "terms, simple indicators, multi-step patterns, and question complexity. The weighted score determines the tier:";

const CUSTOM_PROMPT_WITH_HEURISTIC_FALLBACK =
  "This router classifies with your own prompt, so the tier comes from whatever rubric it states. The four tier " +
  "names stay fixed. The scoring below is the heuristic, which now runs only when the classifier call fails:";

const CUSTOM_PROMPT_WITH_DEFAULT_MODEL_FALLBACK =
  "This router classifies with your own prompt, so the tier comes from whatever rubric it states. The four tier " +
  "names stay fixed. The scoring below no longer runs at all, since a failed classifier routes to the default " +
  "model instead:";

/**
 * What the scoring breakdown below it actually describes. A custom prompt means the score no longer
 * decides the tier, and pairing one with the default-model fallback means the heuristic never runs
 * at all, so the panel must not keep implying a score is involved on either router.
 */
const scoringExplanation = (
  value: ComplexityRouterConfigValue,
  t: (key: string, opts?: { defaultValue?: string }) => string = (key: string) => key.replace(/^ui\./, ""),
): string => {
  const usesCustomPrompt =
    value.classifier_type === "llm" && Boolean(value.classifier_llm_config?.system_prompt?.trim());
  if (!usesCustomPrompt)
    return t(`ui.${DEFAULT_SCORING_EXPLANATION}`, { defaultValue: DEFAULT_SCORING_EXPLANATION });
  return value.classifier_fallback === "default_model"
    ? t(`ui.${CUSTOM_PROMPT_WITH_DEFAULT_MODEL_FALLBACK}`, { defaultValue: CUSTOM_PROMPT_WITH_DEFAULT_MODEL_FALLBACK })
    : t(`ui.${CUSTOM_PROMPT_WITH_HEURISTIC_FALLBACK}`, { defaultValue: CUSTOM_PROMPT_WITH_HEURISTIC_FALLBACK });
};

interface ClassificationMethodConfigProps {
  value: ComplexityRouterConfigValue;
  onChange: (value: ComplexityRouterConfigValue) => void;
  modelOptions: { value: string; label: string }[];
  customTechnicalKeywords?: string[];
  onCustomTechnicalKeywordsChange?: (keywords: string[]) => void;
  showValidationErrors?: boolean;
  /** Enables the default-model fallback, which the backend rejects without a default model. */
  hasDefaultModel?: boolean;
}

const ClassificationMethodConfig: React.FC<ClassificationMethodConfigProps> = ({
  value,
  onChange,
  modelOptions,
  customTechnicalKeywords,
  onCustomTechnicalKeywordsChange,
  showValidationErrors = false,
  hasDefaultModel = false,
}) => {
  const { t } = useTranslation();
  const classifierModelMissing =
    showValidationErrors && value.classifier_type === "llm" && !value.classifier_llm_config?.model;
  const usesCustomPrompt = Boolean(value.classifier_llm_config?.system_prompt?.trim());
  const classificationRubric = value.classifier_llm_config?.classification_rubric ?? DEFAULT_CLASSIFICATION_RUBRIC;

  const handleClassifierTypeChange = (classifierType: ClassifierType) => {
    const nextValue: ComplexityRouterConfigValue = {
      ...value,
      classifier_type: classifierType,
      classifier_llm_config:
        classifierType === "llm"
          ? value.classifier_llm_config ?? {
              model: "",
              timeout_ms: DEFAULT_CLASSIFIER_TIMEOUT_MS,
              classification_rubric: NEW_CLASSIFIER_CLASSIFICATION_RUBRIC,
            }
          : undefined,
      classifier_context_window_size:
        classifierType === "llm"
          ? value.classifier_context_window_size ?? DEFAULT_CLASSIFIER_CONTEXT_WINDOW_SIZE
          : undefined,
      classifier_context_per_turn_chars:
        classifierType === "llm"
          ? value.classifier_context_per_turn_chars ?? DEFAULT_CLASSIFIER_CONTEXT_PER_TURN_CHARS
          : undefined,
      classifier_context_include_assistant_turns:
        classifierType === "llm" ? value.classifier_context_include_assistant_turns : undefined,
      classifier_fallback: classifierType === "llm" ? value.classifier_fallback : undefined,
    };
    onChange(nextValue);
  };

  const handleClassifierModelChange = (model: string) => {
    onChange({
      ...value,
      classifier_llm_config: {
        ...value.classifier_llm_config,
        model,
        timeout_ms: value.classifier_llm_config?.timeout_ms ?? DEFAULT_CLASSIFIER_TIMEOUT_MS,
      },
    });
  };

  const handleClassifierTimeoutChange = (timeoutMs: number | null) => {
    onChange({
      ...value,
      classifier_llm_config: {
        ...value.classifier_llm_config,
        model: value.classifier_llm_config?.model ?? "",
        timeout_ms: timeoutMs ?? DEFAULT_CLASSIFIER_TIMEOUT_MS,
      },
    });
  };

  const handleClassificationRubricChange = (classificationRubric: ClassificationRubric) => {
    onChange({
      ...value,
      classifier_llm_config: {
        ...value.classifier_llm_config,
        model: value.classifier_llm_config?.model ?? "",
        timeout_ms: value.classifier_llm_config?.timeout_ms ?? DEFAULT_CLASSIFIER_TIMEOUT_MS,
        classification_rubric: classificationRubric,
      },
    });
  };

  const handleClassifierSystemPromptChange = (systemPrompt: string | undefined) => {
    onChange({
      ...value,
      classifier_llm_config: {
        ...value.classifier_llm_config,
        model: value.classifier_llm_config?.model ?? "",
        timeout_ms: value.classifier_llm_config?.timeout_ms ?? DEFAULT_CLASSIFIER_TIMEOUT_MS,
        system_prompt: systemPrompt,
      },
    });
  };

  const handleClassifierFallbackChange = (fallback: ClassifierFallback) => {
    onChange({ ...value, classifier_fallback: fallback });
  };

  const handleClassifierContextWindowSizeChange = (windowSize: number | null) => {
    onChange({
      ...value,
      classifier_context_window_size: windowSize ?? DEFAULT_CLASSIFIER_CONTEXT_WINDOW_SIZE,
    });
  };

  const handleClassifierContextPerTurnCharsChange = (perTurnChars: number | null) => {
    onChange({
      ...value,
      classifier_context_per_turn_chars: perTurnChars ?? DEFAULT_CLASSIFIER_CONTEXT_PER_TURN_CHARS,
    });
  };

  const handleClassifierContextIncludeAssistantTurnsChange = (includeAssistantTurns: boolean) => {
    onChange({
      ...value,
      classifier_context_include_assistant_turns: includeAssistantTurns,
    });
  };

  return (
    <>
      <Radio.Group
        value={value.classifier_type}
        onChange={(e) => handleClassifierTypeChange(e.target.value)}
        className="w-full"
      >
        <Space direction="vertical" className="w-full">
          <Radio value="heuristic">
            <Text strong>{t("ui.Heuristic")}</Text>{" "}
            <Text type="secondary">{t("ui.(default) — rule-based scoring, no API calls, <1ms latency")}</Text>
          </Radio>
          <Radio value="llm">
            <Text strong>{t("ui.LLM Classifier")}</Text>{" "}
            <Text type="secondary">{t("ui.— use a model to decide the tier (e.g. a small/fast model)")}</Text>
          </Radio>
        </Space>
      </Radio.Group>

      {value.classifier_type === "llm" && (
        <div className="mt-4 space-y-3">
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.Classifier Model")}
            </Text>
            <AntdSelect
              value={value.classifier_llm_config?.model || undefined}
              onChange={handleClassifierModelChange}
              placeholder={t("ui.Select the model that will classify request complexity")}
              showSearch
              style={{ width: "100%" }}
              options={modelOptions}
              status={classifierModelMissing ? "error" : undefined}
            />
            {classifierModelMissing && (
              <Text type="danger" style={{ fontSize: 12 }}>
                {t("ui.A classifier model is required")}
              </Text>
            )}
          </div>
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.Timeout (ms)")}
            </Text>
            <InputNumber
              value={value.classifier_llm_config?.timeout_ms ?? DEFAULT_CLASSIFIER_TIMEOUT_MS}
              onChange={handleClassifierTimeoutChange}
              min={1}
              style={{ width: "100%" }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.How long the classifier call has before it fails and the fallback below takes over.")}
            </Text>
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Text strong>{t("ui.Classification Rubric")}</Text>
              <Tooltip title={t("ui.Every rubric uses the same four tiers and the same tier definitions. They differ only in the worked examples that show the classifier where the boundary between tiers sits.")}>
                <InfoCircleOutlined className="text-gray-400" />
              </Tooltip>
            </div>
            <Tooltip title={usesCustomPrompt ? t("ui.Your custom prompt replaces the built-in rubric entirely") : undefined}>
              <AntdSelect
                value={classificationRubric}
                onChange={handleClassificationRubricChange}
                disabled={usesCustomPrompt}
                style={{ width: "100%" }}
                aria-label={t("ui.Classification Rubric")}
                options={CLASSIFICATION_RUBRIC_KEYS.map((preset) => ({
                  value: preset,
                  label: t(`ui.${CLASSIFICATION_RUBRIC_DESCRIPTIONS[preset].label}`, {
                    defaultValue: CLASSIFICATION_RUBRIC_DESCRIPTIONS[preset].label,
                  }),
                }))}
              />
            </Tooltip>
            <Text type="secondary" style={{ display: "block", fontSize: 12 }}>
              {usesCustomPrompt
                ? t("ui.Not in use: the custom prompt below is the classifier's entire rubric.")
                : t(`ui.${CLASSIFICATION_RUBRIC_DESCRIPTIONS[classificationRubric].description}`, {
                    defaultValue: CLASSIFICATION_RUBRIC_DESCRIPTIONS[classificationRubric].description,
                  })}
            </Text>
          </div>
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.Classifier Prompt")}
            </Text>
            <ClassifierPromptEditor
              systemPrompt={value.classifier_llm_config?.system_prompt}
              onChange={handleClassifierSystemPromptChange}
              contextWindowSize={value.classifier_context_window_size ?? DEFAULT_CLASSIFIER_CONTEXT_WINDOW_SIZE}
              tierLabels={value.tier_labels}
              classificationRubric={classificationRubric}
            />
          </div>
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.If the classifier fails")}
            </Text>
            <Radio.Group
              value={value.classifier_fallback ?? DEFAULT_CLASSIFIER_FALLBACK}
              onChange={(e) => handleClassifierFallbackChange(e.target.value)}
            >
              <Space direction="vertical">
                <Radio value="heuristic">
                  <Text>{t("ui.Score with the heuristic")}</Text>{" "}
                  <Text type="secondary">{t("ui.— right when the classifier grades complexity too")}</Text>
                </Radio>
                <Radio value="default_model" disabled={!hasDefaultModel}>
                  <Tooltip
                    title={hasDefaultModel ? undefined : t("ui.Set a default model on this router to use this option")}
                  >
                    <span>
                      <Text>{t("ui.Route to the default model")}</Text>{" "}
                      <Text type="secondary">{t("ui.— right when your prompt grades something other than complexity")}</Text>
                    </span>
                  </Tooltip>
                </Radio>
              </Space>
            </Radio.Group>
            <Text type="secondary" style={{ display: "block", fontSize: 12 }}>
              {t("ui.Applies when the classifier call errors, times out, or returns an unparseable response.")}
            </Text>
          </div>
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.Context Window Size")}
            </Text>
            <InputNumber
              value={value.classifier_context_window_size ?? DEFAULT_CLASSIFIER_CONTEXT_WINDOW_SIZE}
              onChange={handleClassifierContextWindowSizeChange}
              min={0}
              style={{ width: "100%" }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.Number of prior user turns (tool output and harness reminders excluded) sent to the classifier as context, so a referring follow-up like \"now do the same for the streaming path\" is classified against what it refers to. Set to 0 to send only the current message.")}
            </Text>
          </div>
          <div>
            <Text strong style={{ display: "block", marginBottom: 4 }}>
              {t("ui.Context Per-Turn Character Limit")}
            </Text>
            <InputNumber
              value={value.classifier_context_per_turn_chars ?? DEFAULT_CLASSIFIER_CONTEXT_PER_TURN_CHARS}
              onChange={handleClassifierContextPerTurnCharsChange}
              min={1}
              style={{ width: "100%" }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.Prior turns longer than this are truncated.")}
            </Text>
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Switch
                checked={value.classifier_context_include_assistant_turns ?? false}
                onChange={handleClassifierContextIncludeAssistantTurnsChange}
                size="small"
                aria-label={t("ui.Include Assistant Turns")}
              />
              <Text strong>{t("ui.Include Assistant Turns")}</Text>
              <Tooltip title={t("ui.Off by default. Enabling it changes tier decisions, and therefore spend, for an existing router, and sends assistant text to the classifier model, which may be a different provider than the routed model.")}>
                <InfoCircleOutlined className="text-gray-400" />
              </Tooltip>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("ui.Let the classifier read the assistant's replies, so difficulty the model stated rather than the user stays visible: a plan the assistant calls complex, approved with \"yes\", is classified on the work being approved. Context Window Size then counts the last N turns across both roles rather than the last N user turns.")}
            </Text>
          </div>
        </div>
      )}

      {value.classifier_type === "heuristic" && (
        <div className="mt-4">
          <div className="flex items-center gap-2 mb-1">
            <Text strong>{t("ui.Custom Technical Keywords")}</Text>
            <Tooltip title={t("ui.Domain-specific terms appended to the built-in technical keyword list. Prompts containing these terms score higher on the technical dimension and route to more capable models.")}>
              <InfoCircleOutlined className="text-gray-400" />
            </Tooltip>
          </div>
          <Text type="secondary" style={{ display: "block", marginBottom: 8, fontSize: 12 }}>
            {t("ui.Optional: Add terms to the built-in list to improve classification accuracy on the technical dimension. (e.g., udp, kafka, terraform).")}
          </Text>
          <AntdSelect
            mode="tags"
            value={customTechnicalKeywords ?? []}
            onChange={(keywords: string[]) => onCustomTechnicalKeywordsChange?.(keywords)}
            placeholder={t("ui.Type a keyword and press Enter, or paste a comma-separated list")}
            tokenSeparators={[","]}
            open={false}
            suffixIcon={null}
            style={{ width: "100%" }}
            allowClear
          />
        </div>
      )}

      <Card className="bg-gray-50 mt-4">
        <Text strong style={{ display: "block", marginBottom: 8 }}>
          {t("ui.How Classification Works")}
        </Text>
        <Text type="secondary" style={{ fontSize: 13 }}>
          {scoringExplanation(value, t)}
        </Text>
        <ul style={{ marginTop: 8, marginBottom: 0, paddingLeft: 20, fontSize: 13, color: "rgba(0, 0, 0, 0.45)" }}>
          <li>
            <strong>{t(`ui.${effectiveTierLabel("SIMPLE", value.tier_labels)}`, { defaultValue: effectiveTierLabel("SIMPLE", value.tier_labels) })}</strong>:{" "}
            {t("ui.Score < 0.15")}
          </li>
          <li>
            <strong>{t(`ui.${effectiveTierLabel("MEDIUM", value.tier_labels)}`, { defaultValue: effectiveTierLabel("MEDIUM", value.tier_labels) })}</strong>:{" "}
            {t("ui.Score 0.15 - 0.35")}
          </li>
          <li>
            <strong>{t(`ui.${effectiveTierLabel("COMPLEX", value.tier_labels)}`, { defaultValue: effectiveTierLabel("COMPLEX", value.tier_labels) })}</strong>:{" "}
            {t("ui.Score 0.35 - 0.60")}
          </li>
          <li>
            <strong>{t(`ui.${effectiveTierLabel("REASONING", value.tier_labels)}`, { defaultValue: effectiveTierLabel("REASONING", value.tier_labels) })}</strong>:{" "}
            {t("ui.Score > 0.60 (or 2+ reasoning markers)")}
          </li>
        </ul>
      </Card>
    </>
  );
};

export default ClassificationMethodConfig;
