// 本文件提供解析快照预览、浏览器下载和受控导入交互。
"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Loader2, Upload } from "lucide-react";
import { useState } from "react";

import NotificationsManager from "@/components/molecules/notifications_manager";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

import { accountPoolKeys, getParserSnapshot, importParserSnapshot } from "../api";
import type { ChannelSummary, ParserSnapshotDocument } from "../types";
import { AccountPoolQueryState } from "./AccountPoolPanel";

interface SnapshotDialogProps {
  accessToken: string;
  channel: ChannelSummary;
  onClose: () => void;
  onImported: () => Promise<void>;
}

const SnapshotPreview = ({
  loading,
  failed,
  document,
}: {
  loading: boolean;
  failed: boolean;
  document: ParserSnapshotDocument | undefined;
}) => {
  if (loading) {
    return <AccountPoolQueryState kind="loading" message="正在读取解析快照" className="min-h-80" />;
  }
  if (failed || !document) {
    return <AccountPoolQueryState kind="empty" message="当前渠道还没有可预览的解析快照" className="min-h-40" />;
  }
  return (
    <pre className="max-h-[55vh] overflow-auto rounded-md bg-muted p-4 text-xs whitespace-pre-wrap">
      {JSON.stringify(document, null, 2)}
    </pre>
  );
};

export default function SnapshotDialog({ accessToken, channel, onClose, onImported }: SnapshotDialogProps) {
  const [editedDocument, setEditedDocument] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const snapshotQuery = useQuery({
    queryKey: accountPoolKeys.snapshot(channel.channel_id),
    queryFn: () => getParserSnapshot(accessToken, channel.channel_id),
  });
  const displayedDocument = editedDocument ?? (snapshotQuery.data ? JSON.stringify(snapshotQuery.data, null, 2) : "");

  const importMutation = useMutation({
    mutationFn: async () => {
      const document = JSON.parse(displayedDocument) as ParserSnapshotDocument;
      if (!(channel.channel_id in document)) throw new Error("导入文档必须包含当前 channel_id");
      return importParserSnapshot(accessToken, channel.channel_id, document, reason.trim());
    },
    onSuccess: async () => {
      NotificationsManager.success("快照已转换为人工修正");
      await onImported();
      onClose();
    },
    onError: (error) => NotificationsManager.fromBackend(error),
  });

  const download = () => {
    if (!snapshotQuery.data) return;
    const blob = new Blob([JSON.stringify(snapshotQuery.data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${channel.channel_id}-snapshot.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>解析快照</DialogTitle>
          <DialogDescription>快照以 channel_id 为键，不包含 URL、Key 或内部凭证引用</DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="preview">
          <TabsList>
            <TabsTrigger value="preview">预览与导出</TabsTrigger>
            <TabsTrigger value="import">导入修正</TabsTrigger>
          </TabsList>
          <TabsContent value="preview" className="pt-3">
            <SnapshotPreview
              loading={snapshotQuery.isLoading}
              failed={snapshotQuery.isError}
              document={snapshotQuery.data}
            />
          </TabsContent>
          <TabsContent value="import" className="grid gap-4 pt-3">
            <div className="grid gap-2">
              <Label htmlFor="snapshot-document">单渠道快照 JSON</Label>
              <Textarea
                id="snapshot-document"
                className="min-h-80 font-mono text-xs"
                value={displayedDocument}
                onChange={(event) => setEditedDocument(event.target.value)}
                spellCheck={false}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="snapshot-reason">导入原因</Label>
              <Input
                id="snapshot-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="记录本次人工核对依据"
                maxLength={1000}
              />
            </div>
            <Button
              className="w-fit"
              onClick={() => importMutation.mutate()}
              disabled={importMutation.isPending || !displayedDocument || !reason.trim()}
            >
              {importMutation.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              受控导入
            </Button>
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
          <Button onClick={download} disabled={!snapshotQuery.data}>
            <Download />
            下载 JSON
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
