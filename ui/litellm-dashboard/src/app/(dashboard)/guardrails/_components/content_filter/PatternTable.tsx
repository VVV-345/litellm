import React from "react";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/shared/DataTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ACTION_ITEMS } from "./action_options";
import { translateUiText } from "@/utils/i18nText";

interface Pattern {
  id: string;
  type: "prebuilt" | "custom";
  name: string;
  display_name?: string;
  pattern?: string;
  action: "BLOCK" | "MASK";
}

interface PatternTableProps {
  patterns: Pattern[];
  onActionChange: (id: string, action: "BLOCK" | "MASK") => void;
  onRemove: (id: string) => void;
}

const PatternTable: React.FC<PatternTableProps> = ({ patterns, onActionChange, onRemove }) => {
  const { t } = useTranslation();
  const ui = (text: string) => translateUiText(t, text);
  const columns: ColumnDef<Pattern>[] = [
    {
      header: ui("Type"),
      accessorKey: "type",
      size: 100,
      cell: ({ row }) => <Badge variant="secondary">{ui(row.original.type === "prebuilt" ? "Prebuilt" : "Custom")}</Badge>,
    },
    {
      header: ui("Pattern name"),
      accessorKey: "name",
      cell: ({ row }) => row.original.display_name || row.original.name,
    },
    {
      header: ui("Regex pattern"),
      accessorKey: "pattern",
      cell: ({ row }) =>
        row.original.pattern ? (
          <code className="rounded-sm bg-muted px-1 py-0.5 text-xs">{row.original.pattern.substring(0, 40)}...</code>
        ) : (
          "-"
        ),
    },
    {
      header: ui("Action"),
      accessorKey: "action",
      size: 150,
      cell: ({ row }) => (
        <Select
          items={ACTION_ITEMS}
          value={row.original.action}
          onValueChange={(value: string | null) => value && onActionChange(row.original.id, value as "BLOCK" | "MASK")}
        >
          <SelectTrigger size="sm" className="w-[120px]" aria-label={ui("Action")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ACTION_ITEMS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {ui(item.label)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ),
    },
    {
      header: "",
      id: "actions",
      size: 100,
      cell: ({ row }) => (
        <Button variant="ghost" size="sm" onClick={() => onRemove(row.original.id)}>
          <Trash2 />
          {ui("Delete")}
        </Button>
      ),
    },
  ];

  if (patterns.length === 0) {
    return <div className="py-10 text-center text-muted-foreground">{ui("No patterns added.")}</div>;
  }

  return <DataTable data={patterns} columns={columns} getRowId={(row) => row.id} size="compact" />;
};

export default PatternTable;
