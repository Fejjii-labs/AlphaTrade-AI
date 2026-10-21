import * as React from "react";

import { cn } from "@/lib/utils";

export interface DataTableColumn<Row> {
  id: string;
  header: string;
  /** Right-align and use tabular numerals. */
  numeric?: boolean;
  cell: (row: Row) => React.ReactNode;
  className?: string;
}

export interface DataTableProps<Row> {
  columns: Array<DataTableColumn<Row>>;
  rows: Row[];
  getRowId: (row: Row) => string;
  caption?: string;
  empty?: React.ReactNode;
  loading?: boolean;
  skeletonRows?: number;
  className?: string;
}

function SkeletonRows({ columns, count }: { columns: number; count: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, row) => (
        <tr key={row} data-testid="data-table-skeleton-row">
          {Array.from({ length: columns }).map((__, column) => (
            <td key={column} className="px-3 py-3">
              <span className="block h-3 animate-pulse rounded-control bg-surface-2 motion-reduce:animate-none" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/** Dense data table: sticky muted headers, mono numbers, hover, empty and skeleton rows. */
export function DataTable<Row>({
  columns,
  rows,
  getRowId,
  caption,
  empty,
  loading = false,
  skeletonRows = 4,
  className,
}: DataTableProps<Row>) {
  return (
    <div className={cn("overflow-x-auto rounded-card border border-border-subtle", className)}>
      <table
        className="w-full border-collapse text-left"
        data-testid="data-table"
        aria-busy={loading || undefined}
      >
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.id}
                scope="col"
                className={cn(
                  "sticky top-0 z-10 bg-surface-2 px-3 py-2 text-[0.8125rem] font-medium uppercase tracking-wide text-text-muted",
                  column.numeric ? "text-end" : "text-start",
                  column.className,
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <SkeletonRows columns={columns.length} count={skeletonRows} />
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-8 text-center text-text-muted">
                {empty ?? "Nothing to show yet."}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={getRowId(row)}
                className="border-t border-border-subtle hover:bg-surface-2"
                data-testid={`data-table-row-${getRowId(row)}`}
              >
                {columns.map((column) => (
                  <td
                    key={column.id}
                    className={cn(
                      "px-3 py-2 text-[length:var(--text-body)] text-text-primary",
                      column.numeric && "text-end font-data",
                      column.className,
                    )}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
