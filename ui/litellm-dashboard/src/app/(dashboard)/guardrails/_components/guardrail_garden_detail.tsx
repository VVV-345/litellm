import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import AddGuardrailForm from "./add_guardrail_form";
import { Logo } from "@/components/molecules/logo/Logo";
import { GUARDRAIL_PRESETS } from "./guardrail_garden_configs";
import { GuardrailCardInfo } from "./guardrail_garden_data";

interface GuardrailDetailViewProps {
  card: GuardrailCardInfo;
  onBack: () => void;
  accessToken: string | null;
  onGuardrailCreated: () => void;
}

const GuardrailDetailView: React.FC<GuardrailDetailViewProps> = ({ card, onBack, accessToken, onGuardrailCreated }) => {
  const { t } = useTranslation();
  const [isAddFormVisible, setIsAddFormVisible] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");

  const detailRows = [
    { property: t("ui.Provider"), value: card.category === "litellm" ? t("ui.LiteLLM Content Filter") : t("ui.Partner Guardrail") },
    ...(card.subcategory ? [{ property: t("ui.Subcategory"), value: card.subcategory }] : []),
    ...(card.category === "litellm" ? [{ property: t("ui.Cost"), value: t("ui.$0 / request") }] : []),
    ...(card.category === "litellm" ? [{ property: t("ui.External Dependencies"), value: t("ui.None") }] : []),
    ...(card.category === "litellm" ? [{ property: t("ui.Latency"), value: card.eval?.latency || t("ui.<1ms") }] : []),
  ];

  const evalRows = card.eval
    ? [
        { metric: t("ui.Precision"), value: `${card.eval.precision}%` },
        { metric: t("ui.Recall"), value: `${card.eval.recall}%` },
        { metric: t("ui.F1 Score"), value: `${card.eval.f1}%` },
        { metric: t("ui.Test Cases"), value: String(card.eval.testCases) },
        { metric: t("ui.False Positives"), value: "0" },
        { metric: t("ui.False Negatives"), value: "0" },
        { metric: t("ui.Latency (p50)"), value: card.eval.latency },
      ]
    : [];

  const tabs = [{ key: "overview", label: t("ui.Overview") }, ...(card.eval ? [{ key: "eval", label: t("ui.Eval Results") }] : [])];

  return (
    <div style={{ maxWidth: 960, margin: "0 auto" }}>
      {/* Back link */}
      <div
        onClick={onBack}
        className="mb-6 inline-flex cursor-pointer items-center gap-1.5 text-sm text-muted-foreground"
      >
        <ArrowLeft className="size-3" />
        <span>{t(`ui.${card.name}`, { defaultValue: card.name })}</span>
      </div>

      {/* ── Header block (Vertex-style) ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8 }}>
        <Logo src={card.logo} label={t(`ui.${card.name}`, { defaultValue: card.name })} className="w-10 h-10 rounded-lg object-contain shrink-0" />
        <h1 style={{ fontSize: 28, fontWeight: 400, color: "#202124", margin: 0, lineHeight: 1.2 }}>{t(`ui.${card.name}`, { defaultValue: card.name })}</h1>
      </div>

      <p style={{ fontSize: 14, color: "#5f6368", margin: "0 0 20px 0", lineHeight: 1.6 }}>{t(`ui.${card.description}`, { defaultValue: card.description })}</p>

      {/* Action buttons — outlined style like Vertex */}
      <div className="mb-8 flex gap-2.5">
        <Button variant="outline" className="rounded-full" onClick={() => setIsAddFormVisible(true)}>
          {t("ui.Create Guardrail")}
        </Button>
      </div>

      {/* ── Tab bar ──────────────────────────────────── */}
      <div style={{ borderBottom: "1px solid #dadce0", marginBottom: 28 }}>
        <div style={{ display: "flex", gap: 0 }}>
          {tabs.map((tab) => (
            <div
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                padding: "12px 20px",
                fontSize: 14,
                color: activeTab === tab.key ? "#1a73e8" : "#5f6368",
                borderBottom: activeTab === tab.key ? "3px solid #1a73e8" : "3px solid transparent",
                cursor: "pointer",
                fontWeight: activeTab === tab.key ? 500 : 400,
                marginBottom: -1,
              }}
            >
              {tab.label}
            </div>
          ))}
        </div>
      </div>

      {/* ── Tab content ──────────────────────────────── */}
      {activeTab === "overview" && (
        <div style={{ display: "flex", gap: 64 }}>
          {/* Left column — overview + details table */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 style={{ fontSize: 18, fontWeight: 400, color: "#202124", margin: "0 0 12px 0" }}>{t("ui.Overview")}</h2>
            <p style={{ fontSize: 14, color: "#3c4043", lineHeight: 1.7, margin: "0 0 32px 0" }}>{t(`ui.${card.description}`, { defaultValue: card.description })}</p>

            <h2 style={{ fontSize: 18, fontWeight: 400, color: "#202124", margin: "0 0 4px 0" }}>{t("ui.Guardrail Details")}</h2>
            <p style={{ fontSize: 13, color: "#5f6368", margin: "0 0 16px 0" }}>{t("ui.Details are as follows")}</p>

            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #dadce0" }}>
                  <th style={{ textAlign: "left", padding: "12px 0", color: "#5f6368", fontWeight: 500, width: 200 }}>
                    {t("ui.Property")}
                  </th>
                  <th style={{ textAlign: "left", padding: "12px 0", color: "#5f6368", fontWeight: 500 }}>
                    {t(`ui.${card.name}`, { defaultValue: card.name })}
                  </th>
                </tr>
              </thead>
              <tbody>
                {detailRows.map((row, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f3f4" }}>
                    <td style={{ padding: "12px 0", color: "#3c4043" }}>{row.property}</td>
                    <td style={{ padding: "12px 0", color: "#202124" }}>{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Right column — metadata sidebar like Vertex */}
          <div style={{ width: 240, flexShrink: 0 }}>
            {/* Guardrail ID */}
            <div style={{ marginBottom: 28 }}>
              <div style={{ fontSize: 12, color: "#5f6368", marginBottom: 4 }}>{t("ui.Guardrail ID")}</div>
              <div style={{ fontSize: 13, color: "#202124", wordBreak: "break-all" }}>litellm/{card.id}</div>
            </div>

            {/* Type */}
            <div style={{ marginBottom: 28 }}>
              <div style={{ fontSize: 12, color: "#5f6368", marginBottom: 4 }}>{t("ui.Type")}</div>
              <div style={{ fontSize: 13, color: "#202124" }}>
                {card.category === "litellm" ? t("ui.Content Filter") : t("ui.Partner")}
              </div>
            </div>

            {/* Tags — pill style like Vertex */}
            {card.tags.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <div style={{ fontSize: 12, color: "#5f6368", marginBottom: 8 }}>{t("ui.Tags")}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {card.tags.map((tag) => (
                    <span
                      key={tag}
                      style={{
                        fontSize: 12,
                        padding: "4px 12px",
                        borderRadius: 16,
                        border: "1px solid #dadce0",
                        color: "#3c4043",
                        backgroundColor: "#fff",
                      }}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === "eval" && (
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 400, color: "#202124", margin: "0 0 16px 0" }}>{t("ui.Eval Results")}</h2>
          <table style={{ width: "100%", maxWidth: 560, borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ backgroundColor: "#f8f9fa", borderBottom: "1px solid #dadce0" }}>
                <th style={{ textAlign: "left", padding: "12px 16px", color: "#5f6368", fontWeight: 500 }}>{t("ui.Metric")}</th>
                <th style={{ textAlign: "left", padding: "12px 16px", color: "#5f6368", fontWeight: 500 }}>{t("ui.Value")}</th>
              </tr>
            </thead>
            <tbody>
              {evalRows.map((row, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #f1f3f4" }}>
                  <td style={{ padding: "12px 16px", color: "#3c4043" }}>{row.metric}</td>
                  <td style={{ padding: "12px 16px", color: "#202124", fontWeight: 500 }}>{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AddGuardrailForm
        visible={isAddFormVisible}
        onClose={() => setIsAddFormVisible(false)}
        accessToken={accessToken}
        onSuccess={() => {
          setIsAddFormVisible(false);
          onGuardrailCreated();
        }}
        preset={GUARDRAIL_PRESETS[card.id]}
      />
    </div>
  );
};

export default GuardrailDetailView;
