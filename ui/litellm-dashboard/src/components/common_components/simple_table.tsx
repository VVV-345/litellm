import React from "react";
import { useTranslation } from "react-i18next";
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from "@/components/ui/table";

export interface SimpleTableColumn<T> {
  header: string;
  accessor?: keyof T;
  cell?: (row: T) => React.ReactNode;
  width?: string;
}

interface SimpleTableProps<T> {
  data: T[];
  columns: SimpleTableColumn<T>[];
  isLoading?: boolean;
  loadingMessage?: string;
  emptyMessage?: string;
  getRowKey?: (row: T, index: number) => string;
}

/**
 * Simple table component for forms and settings pages
 * For complex tables with sorting/filtering, use DataTable from view_logs
 */
export function SimpleTable<T>({
  data,
  columns,
  isLoading = false,
  loadingMessage,
  emptyMessage,
  getRowKey,
}: SimpleTableProps<T>) {
  const { t } = useTranslation();
  const resolvedLoadingMessage = loadingMessage ?? t("common.loading");
  const resolvedEmptyMessage = emptyMessage ?? t("common.noData");
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column, index) => (
            <TableHead key={index} style={{ width: column.width }}>
              {column.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <TableRow>
            <TableCell colSpan={columns.length} className="text-center">
              <span className="text-gray-500">{resolvedLoadingMessage}</span>
            </TableCell>
          </TableRow>
        ) : data.length > 0 ? (
          data.map((row, rowIndex) => (
            <TableRow key={getRowKey ? getRowKey(row, rowIndex) : rowIndex}>
              {columns.map((column, colIndex) => (
                <TableCell key={colIndex}>
                  {column.cell ? column.cell(row) : String(row[column.accessor as keyof T] ?? "")}
                </TableCell>
              ))}
            </TableRow>
          ))
        ) : (
          <TableRow>
            <TableCell colSpan={columns.length} className="text-center">
              <span className="text-gray-500">{resolvedEmptyMessage}</span>
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}
