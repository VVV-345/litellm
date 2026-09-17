import { CostEstimateResponse } from "../types";
import { formatNumberWithCommas } from "@/utils/dataUtils";
import { MultiModelResult } from "./types";
import i18n from "@/i18n";

const ui = (key: string): string => i18n.t(`ui.${key}`);

const formatCostForExport = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return "-";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${formatNumberWithCommas(value, 2)}`;
};

const formatRequestsForExport = (value: number | null | undefined): string => {
  if (value === null || value === undefined) return "-";
  return formatNumberWithCommas(value, 0);
};

const generateModelSection = (result: CostEstimateResponse): string => {
  return `
    <div class="model-section">
      <h3>${result.model} ${result.provider ? `<span class="provider">(${result.provider})</span>` : ""}</h3>
      
      <div class="meta">
        <p><strong>${ui("Input Tokens per Request:")}</strong> ${formatRequestsForExport(result.input_tokens)}</p>
        <p><strong>${ui("Output Tokens per Request:")}</strong> ${formatRequestsForExport(result.output_tokens)}</p>
        ${result.num_requests_per_day ? `<p><strong>${ui("Requests per Day:")}</strong> ${formatRequestsForExport(result.num_requests_per_day)}</p>` : ""}
        ${result.num_requests_per_month ? `<p><strong>${ui("Requests per Month:")}</strong> ${formatRequestsForExport(result.num_requests_per_month)}</p>` : ""}
      </div>

      <table>
        <tr>
          <th>${ui("Cost Type")}</th>
          <th>${ui("Per Request")}</th>
          ${result.daily_cost !== null ? `<th>${ui("Daily")}</th>` : ""}
          ${result.monthly_cost !== null ? `<th>${ui("Monthly")}</th>` : ""}
        </tr>
        <tr>
          <td>${ui("Input Cost")}</td>
          <td class="cost-value">${formatCostForExport(result.input_cost_per_request)}</td>
          ${result.daily_cost !== null ? `<td class="cost-value">${formatCostForExport(result.daily_input_cost)}</td>` : ""}
          ${result.monthly_cost !== null ? `<td class="cost-value">${formatCostForExport(result.monthly_input_cost)}</td>` : ""}
        </tr>
        <tr>
          <td>${ui("Output Cost")}</td>
          <td class="cost-value">${formatCostForExport(result.output_cost_per_request)}</td>
          ${result.daily_cost !== null ? `<td class="cost-value">${formatCostForExport(result.daily_output_cost)}</td>` : ""}
          ${result.monthly_cost !== null ? `<td class="cost-value">${formatCostForExport(result.monthly_output_cost)}</td>` : ""}
        </tr>
        <tr>
          <td>${ui("Margin/Fee")}</td>
          <td class="cost-value">${formatCostForExport(result.margin_cost_per_request)}</td>
          ${result.daily_cost !== null ? `<td class="cost-value">${formatCostForExport(result.daily_margin_cost)}</td>` : ""}
          ${result.monthly_cost !== null ? `<td class="cost-value">${formatCostForExport(result.monthly_margin_cost)}</td>` : ""}
        </tr>
        <tr class="total-row">
          <td>${ui("Total")}</td>
          <td class="cost-value">${formatCostForExport(result.cost_per_request)}</td>
          ${result.daily_cost !== null ? `<td class="cost-value">${formatCostForExport(result.daily_cost)}</td>` : ""}
          ${result.monthly_cost !== null ? `<td class="cost-value">${formatCostForExport(result.monthly_cost)}</td>` : ""}
        </tr>
      </table>
    </div>
  `;
};

export const exportMultiToPDF = (multiResult: MultiModelResult): void => {
  const printWindow = window.open("", "_blank");
  if (!printWindow) {
    alert(ui("Please allow popups to export PDF"));
    return;
  }

  const validEntries = multiResult.entries.filter((e) => e.result !== null);
  const modelCount = validEntries.length;

  const html = `
    <!DOCTYPE html>
    <html>
    <head>
      <title>${ui("LLM Multi-Model Cost Estimate Report")}</title>
      <style>
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          padding: 40px;
          max-width: 900px;
          margin: 0 auto;
          color: #333;
        }
        h1 {
          color: #1a1a1a;
          border-bottom: 2px solid #1890ff;
          padding-bottom: 10px;
          margin-bottom: 30px;
        }
        h2 {
          color: #444;
          margin-top: 30px;
          margin-bottom: 15px;
        }
        h3 {
          color: #555;
          margin-top: 25px;
          margin-bottom: 10px;
          padding-bottom: 5px;
          border-bottom: 1px solid #eee;
        }
        .provider {
          font-weight: normal;
          color: #1890ff;
          font-size: 14px;
        }
        .meta {
          background: #f5f5f5;
          padding: 12px 15px;
          border-radius: 8px;
          margin-bottom: 15px;
          font-size: 13px;
        }
        .meta p {
          margin: 4px 0;
        }
        table {
          width: 100%;
          border-collapse: collapse;
          margin-bottom: 20px;
        }
        th, td {
          padding: 10px 12px;
          text-align: left;
          border-bottom: 1px solid #ddd;
        }
        th {
          background: #f8f9fa;
          font-weight: 600;
          font-size: 13px;
        }
        .cost-value {
          font-family: monospace;
          font-size: 13px;
        }
        .total-row {
          font-weight: bold;
          background: #e6f7ff;
        }
        .summary-box {
          background: linear-gradient(135deg, #e6f7ff 0%, #f9f0ff 100%);
          border: 1px solid #91d5ff;
          border-radius: 8px;
          padding: 20px;
          margin-bottom: 30px;
        }
        .summary-box h2 {
          margin-top: 0;
          color: #1890ff;
        }
        .summary-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 20px;
          margin-top: 15px;
        }
        .summary-item {
          text-align: center;
        }
        .summary-item .label {
          font-size: 12px;
          color: #666;
          margin-bottom: 5px;
        }
        .summary-item .value {
          font-size: 20px;
          font-weight: bold;
          font-family: monospace;
        }
        .summary-item .value.blue { color: #1890ff; }
        .summary-item .value.green { color: #52c41a; }
        .summary-item .value.purple { color: #722ed1; }
        .model-section {
          margin-bottom: 30px;
          page-break-inside: avoid;
        }
        .footer {
          margin-top: 40px;
          padding-top: 20px;
          border-top: 1px solid #ddd;
          font-size: 12px;
          color: #666;
        }
        @media print {
          body { padding: 20px; }
          .model-section { page-break-inside: avoid; }
        }
      </style>
    </head>
    <body>
      <h1>${ui("LLM Cost Estimate Report")}</h1>
      <p style="color: #666; margin-top: -20px; margin-bottom: 30px;">${i18n.t("ui.{{count}} models configured", { count: modelCount })}</p>
      
      <div class="summary-box">
        <h2>${ui("Combined Totals")}</h2>
        <div class="summary-grid">
          <div class="summary-item">
            <div class="label">${ui("Total Per Request")}</div>
            <div class="value blue">${formatCostForExport(multiResult.totals.cost_per_request)}</div>
          </div>
          <div class="summary-item">
            <div class="label">${ui("Total Daily")}</div>
            <div class="value green">${formatCostForExport(multiResult.totals.daily_cost)}</div>
          </div>
          <div class="summary-item">
            <div class="label">${ui("Total Monthly")}</div>
            <div class="value purple">${formatCostForExport(multiResult.totals.monthly_cost)}</div>
          </div>
        </div>
        ${
          multiResult.totals.margin_per_request > 0
            ? `
        <div class="summary-grid" style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #ddd;">
          <div class="summary-item">
            <div class="label">${ui("Margin/Request")}</div>
            <div class="value" style="color: #faad14;">${formatCostForExport(multiResult.totals.margin_per_request)}</div>
          </div>
          <div class="summary-item">
            <div class="label">${ui("Daily Margin")}</div>
            <div class="value" style="color: #faad14;">${formatCostForExport(multiResult.totals.daily_margin)}</div>
          </div>
          <div class="summary-item">
            <div class="label">${ui("Monthly Margin")}</div>
            <div class="value" style="color: #faad14;">${formatCostForExport(multiResult.totals.monthly_margin)}</div>
          </div>
        </div>
        `
            : ""
        }
      </div>

      <h2>${ui("Model Breakdown")}</h2>
      ${validEntries.map((e) => generateModelSection(e.result!)).join("")}

      <div class="footer">
        <p>${i18n.t("ui.Generated by LiteLLM Pricing Calculator on {{date}}", { date: new Date().toLocaleString() })}</p>
      </div>
    </body>
    </html>
  `;

  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.onload = () => {
    printWindow.print();
  };
};

export const exportMultiToCSV = (multiResult: MultiModelResult): void => {
  const validEntries = multiResult.entries.filter((e) => e.result !== null);

  const rows: string[][] = [[ui("LLM Multi-Model Cost Estimate Report")], [ui("Generated"), new Date().toLocaleString()], [""]];

  // Summary section
  rows.push(
    [ui("COMBINED TOTALS")],
    [ui("Total Per Request"), multiResult.totals.cost_per_request.toString()],
    [ui("Total Daily"), multiResult.totals.daily_cost?.toString() || "-"],
    [ui("Total Monthly"), multiResult.totals.monthly_cost?.toString() || "-"],
    [ui("Margin Per Request"), multiResult.totals.margin_per_request.toString()],
    [ui("Daily Margin"), multiResult.totals.daily_margin?.toString() || "-"],
    [ui("Monthly Margin"), multiResult.totals.monthly_margin?.toString() || "-"],
    [""],
  );

  // Summary table header
  rows.push([
    ui("Model"),
    ui("Provider"),
    ui("Input Tokens"),
    ui("Output Tokens"),
    ui("Requests/Day"),
    ui("Requests/Month"),
    ui("Cost/Request"),
    ui("Daily Cost"),
    ui("Monthly Cost"),
    ui("Input Cost/Req"),
    ui("Output Cost/Req"),
    ui("Margin/Req"),
  ]);

  // Add each model's data
  for (const entry of validEntries) {
    const r = entry.result!;
    rows.push([
      r.model,
      r.provider || "-",
      r.input_tokens.toString(),
      r.output_tokens.toString(),
      r.num_requests_per_day?.toString() || "-",
      r.num_requests_per_month?.toString() || "-",
      r.cost_per_request.toString(),
      r.daily_cost?.toString() || "-",
      r.monthly_cost?.toString() || "-",
      r.input_cost_per_request.toString(),
      r.output_cost_per_request.toString(),
      r.margin_cost_per_request.toString(),
    ]);
  }

  const csv = rows.map((row) => row.map((cell) => `"${cell}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `cost_estimate_multi_model_${new Date().toISOString().split("T")[0]}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
};
