// 本文件展示解析历史、原始值与有效值差异，并连接人工修正操作。
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PackagePlus, Pencil, ReceiptText } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { accountPoolKeys, getEffectiveData, getParserHistory } from "../api";
import { accountPoolTableColumnDividerClass, formatAccountPoolDateTime } from "../accountPoolPresentation";
import { buildMeteredPriceDrafts } from "../meteredPriceEditor";
import { buildParserFieldRows, formatJsonValue, type ParserFieldRow } from "../parserRows";
import { buildSubscriptionDraft } from "../subscriptionEditor";
import type { EffectiveParserData, ParserRunHistory } from "../types";
import { AccountPoolQueryState } from "./AccountPoolPanel";
import MeteredPriceDialog from "./MeteredPriceDialog";
import OverrideDialog from "./OverrideDialog";
import SubscriptionDialog from "./SubscriptionDialog";

interface ParserDataPanelProps {
  accessToken: string;
  channelId: string;
}

const statusVariant = (status: string): "secondary" | "outline" | "destructive" => {
  if (status === "success" || status === "completed") return "secondary";
  if (status === "partial" || status === "manual_required") return "outline";
  return "destructive";
};

const ValueBlock = ({ value }: { value: ParserFieldRow["rawValue"] }) => (
  <pre className="max-h-32 max-w-[28rem] overflow-auto text-xs whitespace-pre-wrap break-all text-foreground">
    {formatJsonValue(value)}
  </pre>
);

const FieldStatus = ({ field }: { field: ParserFieldRow }) => {
  if (field.activeOverrideId) return <Badge variant="outline">人工修正</Badge>;
  if (field.changed) return <Badge variant="secondary">已变化</Badge>;
  return <span className="text-xs text-muted-foreground">自动</span>;
};

interface ParserFieldsContentProps {
  data: EffectiveParserData | undefined;
  loading: boolean;
  error: boolean;
  rows: ParserFieldRow[];
  discoveredModels: string[];
  onEditMeteredPrice: () => void;
  onEditSubscription: () => void;
  onEditRow: (field: ParserFieldRow) => void;
}

function ParserFieldsContent({
  data,
  loading,
  error,
  rows,
  discoveredModels,
  onEditMeteredPrice,
  onEditSubscription,
  onEditRow,
}: ParserFieldsContentProps) {
  if (loading) return <AccountPoolQueryState kind="loading" message="正在读取解析数据" />;
  if (error || !data) {
    return (
      <AccountPoolQueryState
        kind="empty"
        message="暂无解析数据，运行一次渠道解析后即可查看原始值和人工修正后的有效值"
      />
    );
  }
  return (
    <div className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant={statusVariant(data.parser_status)}>{data.parser_status}</Badge>
        <span>解析时间 {formatAccountPoolDateTime(data.parsed_at)}</span>
        <span>人工修正 {data.active_overrides.length} 项</span>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={discoveredModels.length === 0}
          onClick={onEditSubscription}
        >
          <PackagePlus />
          补充套餐
        </Button>
      </div>
      <Table className={accountPoolTableColumnDividerClass}>
        <TableHeader>
          <TableRow>
            <TableHead className="w-40">字段</TableHead>
            <TableHead>自动解析值</TableHead>
            <TableHead>当前有效值</TableHead>
            <TableHead className="w-24">状态</TableHead>
            <TableHead className="w-20 text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((field) => (
            <TableRow key={field.path}>
              <TableCell className="whitespace-normal">
                <div className="font-medium">{field.label}</div>
                <code className="text-[11px] text-muted-foreground">{field.path}</code>
              </TableCell>
              <TableCell className="whitespace-normal">
                <ValueBlock value={field.rawValue} />
              </TableCell>
              <TableCell className="whitespace-normal">
                <ValueBlock value={field.effectiveValue} />
              </TableCell>
              <TableCell>
                <FieldStatus field={field} />
              </TableCell>
              <TableCell className="text-right">
                {field.path === "/metered" && (
                  <Button variant="outline" size="sm" onClick={onEditMeteredPrice}>
                    <ReceiptText />
                    补充价格
                  </Button>
                )}
                <Button variant="ghost" size="sm" onClick={() => onEditRow(field)}>
                  <Pencil />
                  修正
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

interface ParserHistoryContentProps {
  data: ParserRunHistory | undefined;
  loading: boolean;
  error: boolean;
}

function ParserHistoryContent({ data, loading, error }: ParserHistoryContentProps) {
  if (loading) return <AccountPoolQueryState kind="loading" message="正在读取解析历史" className="min-h-52" />;
  if (error || !data || data.runs.length === 0) {
    return <AccountPoolQueryState kind="empty" message="暂无解析历史" className="min-h-40" />;
  }
  return (
    <Table className={accountPoolTableColumnDividerClass}>
      <TableHeader>
        <TableRow>
          <TableHead>时间</TableHead>
          <TableHead>解析器</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>发现模型</TableHead>
          <TableHead className="text-right">问题</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.runs.map((run) => (
          <TableRow key={run.parser_run_id}>
            <TableCell>{formatAccountPoolDateTime(run.parsed_at)}</TableCell>
            <TableCell>
              {run.parser_id} <span className="text-xs text-muted-foreground">v{run.parser_version}</span>
            </TableCell>
            <TableCell>
              <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
            </TableCell>
            <TableCell className="max-w-80 truncate">{run.discovered_models.join(", ") || "-"}</TableCell>
            <TableCell className="text-right tabular-nums">{run.issues.length}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function ParserDataPanel({ accessToken, channelId }: ParserDataPanelProps) {
  const queryClient = useQueryClient();
  const [editingRow, setEditingRow] = useState<ParserFieldRow | null>(null);
  const [editingMeteredPrice, setEditingMeteredPrice] = useState(false);
  const [editingSubscription, setEditingSubscription] = useState(false);
  const effectiveQuery = useQuery({
    queryKey: accountPoolKeys.effective(channelId),
    queryFn: () => getEffectiveData(accessToken, channelId),
  });
  const historyQuery = useQuery({
    queryKey: accountPoolKeys.history(channelId),
    queryFn: () => getParserHistory(accessToken, channelId),
  });
  const effectiveData = effectiveQuery.data;
  const historyData = historyQuery.data;
  const rows = useMemo(() => {
    if (!effectiveData) return [];
    return buildParserFieldRows(
      effectiveData.raw_result,
      effectiveData.effective_result,
      effectiveData.active_overrides,
    );
  }, [effectiveData]);
  const discoveredModels = historyData?.runs[0]?.discovered_models ?? [];
  const meteredOverrideId =
    effectiveData?.active_overrides.find((override) => override.field_path === "/metered")?.override_id ?? null;
  const subscriptionOverrideId =
    effectiveData?.active_overrides.find((override) => override.field_path === "/subscription")?.override_id ?? null;
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: accountPoolKeys.effective(channelId) }),
      queryClient.invalidateQueries({ queryKey: accountPoolKeys.history(channelId) }),
      queryClient.invalidateQueries({ queryKey: accountPoolKeys.snapshot(channelId) }),
    ]);
  };

  return (
    <>
      <Tabs defaultValue="fields" className="min-w-0">
        <TabsList variant="line">
          <TabsTrigger value="fields">字段差异</TabsTrigger>
          <TabsTrigger value="history">解析历史</TabsTrigger>
        </TabsList>
        <TabsContent value="fields" className="pt-3">
          <ParserFieldsContent
            data={effectiveData}
            loading={effectiveQuery.isLoading}
            error={effectiveQuery.isError}
            rows={rows}
            discoveredModels={discoveredModels}
            onEditMeteredPrice={() => setEditingMeteredPrice(true)}
            onEditSubscription={() => setEditingSubscription(true)}
            onEditRow={setEditingRow}
          />
        </TabsContent>
        <TabsContent value="history" className="pt-3">
          <ParserHistoryContent data={historyData} loading={historyQuery.isLoading} error={historyQuery.isError} />
        </TabsContent>
      </Tabs>

      {editingMeteredPrice && effectiveData && (
        <MeteredPriceDialog
          accessToken={accessToken}
          channelId={channelId}
          initialDrafts={buildMeteredPriceDrafts(effectiveData.effective_result.metered, discoveredModels)}
          expectedOverrideId={meteredOverrideId}
          onClose={() => setEditingMeteredPrice(false)}
          onSaved={refresh}
        />
      )}
      {editingSubscription && effectiveData && (
        <SubscriptionDialog
          accessToken={accessToken}
          channelId={channelId}
          models={discoveredModels}
          initialDraft={buildSubscriptionDraft(effectiveData.effective_result.subscription, discoveredModels)}
          expectedOverrideId={subscriptionOverrideId}
          onClose={() => setEditingSubscription(false)}
          onSaved={refresh}
        />
      )}
      {editingRow && (
        <OverrideDialog
          accessToken={accessToken}
          channelId={channelId}
          row={editingRow}
          onClose={() => setEditingRow(null)}
          onSaved={refresh}
        />
      )}
    </>
  );
}
