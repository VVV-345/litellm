import React from "react";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/shared/DataTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ACTION_ITEMS, SEVERITY_ITEMS } from "./action_options";
import { translateUiText } from "@/utils/i18nText";

interface ContentCategory {
  id: string;
  category: string;
  display_name: string;
  action: "BLOCK" | "MASK";
  severity_threshold: "high" | "medium" | "low";
}

interface CategoryTableProps {
  categories: ContentCategory[];
  onActionChange?: (id: string, action: "BLOCK" | "MASK") => void;
  onSeverityChange?: (id: string, severity: "high" | "medium" | "low") => void;
  onRemove?: (id: string) => void;
  readOnly?: boolean;
}

const CategoryTable: React.FC<CategoryTableProps> = ({
  categories,
  onActionChange,
  onSeverityChange,
  onRemove,
  readOnly = false,
}) => {
  const { t } = useTranslation();
  const ui = (text: string) => translateUiText(t, text);
  const columns: ColumnDef<ContentCategory>[] = [
    {
      header: ui("Category"),
      accessorKey: "display_name",
      cell: ({ row }) => {
        const { category, display_name: displayName } = row.original;
        return (
          <div>
            <span className="font-semibold">{displayName}</span>
            {displayName !== category && <div className="text-xs text-muted-foreground">{category}</div>}
          </div>
        );
      },
    },
    {
      header: ui("Severity Threshold"),
      accessorKey: "severity_threshold",
      size: 180,
      cell: ({ row }) => {
        const { id, severity_threshold: severity } = row.original;
        if (readOnly) {
          return <Badge variant={severity === "high" ? "destructive" : "secondary"}>{severity.toUpperCase()}</Badge>;
        }
        return (
          <Select
            items={SEVERITY_ITEMS}
            value={severity}
            onValueChange={(value: string | null) =>
              value && onSeverityChange?.(id, value as "high" | "medium" | "low")
            }
          >
            <SelectTrigger size="sm" className="w-[150px]" aria-label={ui("Severity Threshold")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SEVERITY_ITEMS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {ui(item.label)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      },
    },
    {
      header: ui("Action"),
      accessorKey: "action",
      size: 150,
      cell: ({ row }) => {
        const { action, id } = row.original;
        if (readOnly) {
          return <Badge variant={action === "BLOCK" ? "destructive" : "secondary"}>{action}</Badge>;
        }
        return (
          <Select
            items={ACTION_ITEMS}
            value={action}
            onValueChange={(value: string | null) => value && onActionChange?.(id, value as "BLOCK" | "MASK")}
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
        );
      },
    },
  ];

  if (!readOnly) {
    columns.push({
      header: "",
      id: "actions",
      size: 100,
      cell: ({ row }) => (
        <Button variant="ghost" size="sm" onClick={() => onRemove?.(row.original.id)}>
          <Trash2 />
          {ui("Delete")}
        </Button>
      ),
    });
  }

  if (categories.length === 0) {
    return <div className="py-10 text-center text-muted-foreground">{ui("No categories configured.")}</div>;
  }

  return <DataTable data={categories} columns={columns} getRowId={(row) => row.id} size="compact" />;
};

export default CategoryTable;
