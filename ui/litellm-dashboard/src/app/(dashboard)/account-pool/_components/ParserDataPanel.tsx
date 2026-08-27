// 本文件展示解析历史、原始值与有效值差异，并连接人工修正操作。
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, ReceiptText } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { accountPoolKeys, getEffectiveData, getParserHistory } from "../api";
import { accountPoolTableColumnDividerClass, formatAccountPoolDateTime } from "../accountPoolPresentation";
import { buildMeteredPriceDrafts } from "../meteredPriceEditor";
import { buildParserFieldRows, formatJsonValue, type ParserFieldRow } from "../parserRows";
import { AccountPoolQueryState } from "./AccountPoolPanel";
import MeteredPriceDialog from "./MeteredPriceDialog";
import OverrideDialog from "./OverrideDialog";

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

export default function ParserDataPanel({ accessToken, channelId }: ParserDataPanelProps) {
  const queryClient = useQueryClient();
  const [editingRow, setEditingRow] = useState<ParserFieldRow | null>(null);
  const [editingMeteredPrice, setEditingMeteredPrice] = useState(false);
  const effectiveQuery = useQuery({
    queryKey: accountPoolKeys.effective(channelId),
    queryFn: () => getEffectiveData(accessToken, channelId),
  });
  const historyQuery = useQuery({
    queryKey: accountPoolKeys.history(channelId),
    queryFn: () => getParserHistory(accessToken, channelId),
  });
  const rows = useMemo(
    () =>
      effectiveQuery.data
        ? buildParserFieldRows(
            effectiveQuery.data.raw_result,
            effectiveQuery.data.effective_result,
            effectiveQuery.data.active_overrides,
          )
        : [],
    [effectiveQuery.data],
  );

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
          {effectiveQuery.isLoading && <AccountPoolQueryState kind="loading" message="正在读取解析数据" />}
          {effectiveQuery.isError && (
            <AccountPoolQueryState
              kind="empty"
              message="暂无解析数据，运行一次渠道解析后即可查看原始值和人工修正后的有效值"
            />
          )}
          {effectiveQuery.data && (
            <div className="min-w-0">
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant={statusVariant(effectiveQuery.data?.parser_status ?? "unknown")}>
                  {effectiveQuery.data?.parser_status}
                </Badge>
                <span>解析时间 {formatAccountPoolDateTime(effectiveQuery.data.parsed_at)}</span>
                <span>人工修正 {effectiveQuery.data!.active_overrides.length} 项</span>
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
                          <Button variant="outline" size="sm" onClick={() => setEditingMeteredPrice(true)}>
                            <ReceiptText />
                            补充价格
                          </Button>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => setEditingRow(field)}>
                          <Pencil />
                          修正
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>
        <TabsContent value="history" className="pt-3">
          {historyQuery.isLoading && (
            <AccountPoolQueryState kind="loading" message="正在读取解析历史" className="min-h-52" />
          )}
          {(historyQuery.isError || historyQuery.data?.runs.length === 0) && (
            <AccountPoolQueryState kind="empty" message="暂无解析历史" className="min-h-40" />
          )}
          {historyQuery.data && historyQuery.data.runs.length > 0 && (
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
                {historyQuery.data?.runs.map((run) => (
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
          )}
        </TabsContent>
      </Tabs>

      {editingMeteredPrice && effectiveQuery.data && (
        <MeteredPriceDialog
          accessToken={accessToken}
          channelId={channelId}
          initialDrafts={buildMeteredPriceDrafts(
            effectiveQuery.data.effective_result.metered,
            historyQuery.data?.runs[0]?.discovered_models ?? [],
          )}
          expectedOverrideId={
            effectiveQuery.data.active_overrides.find((override) => override.field_path === "/metered")?.override_id ??
            null
          }
          onClose={() => setEditingMeteredPrice(false)}
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
