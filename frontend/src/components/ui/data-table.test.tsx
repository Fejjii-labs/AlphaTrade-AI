import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataTable } from "@/components/ui/data-table";
import { Sparkline } from "@/components/ui/sparkline";

type Row = { id: string; symbol: string; pnl: string };

const columns = [
  { id: "symbol", header: "Symbol", cell: (row: Row) => row.symbol },
  { id: "pnl", header: "PnL", numeric: true, cell: (row: Row) => row.pnl },
  {
    id: "trend",
    header: "7D",
    cell: (row: Row) => <Sparkline label={`${row.symbol} 7 day`} values={[1, 2, 3]} tone="positive" />,
  },
];

describe("DataTable", () => {
  it("renders sticky headers, numeric cells, and a sparkline", () => {
    render(
      <DataTable
        caption="Open positions"
        columns={columns}
        rows={[{ id: "btc", symbol: "BTC", pnl: "+60" }]}
        getRowId={(row) => row.id}
      />,
    );
    expect(screen.getByRole("table", { name: "Open positions" })).toBeInTheDocument();
    const pnl = screen.getByRole("columnheader", { name: "PnL" });
    expect(pnl.className).toContain("uppercase");
    expect(pnl.className).toContain("text-end");
    expect(screen.getByTestId("data-table-row-btc")).toHaveTextContent("BTC");
    expect(screen.getByTestId("sparkline")).toHaveAttribute("data-tone", "positive");
  });

  it("shows the empty message when there are no rows", () => {
    render(
      <DataTable columns={columns} rows={[]} getRowId={(row) => row.id} empty="No open positions." />,
    );
    expect(screen.getByText("No open positions.")).toBeInTheDocument();
    expect(screen.queryByTestId(/data-table-row/)).not.toBeInTheDocument();
  });

  it("shows skeleton rows while loading", () => {
    render(
      <DataTable columns={columns} rows={[]} getRowId={(row) => row.id} loading skeletonRows={3} />,
    );
    expect(screen.getByTestId("data-table")).toHaveAttribute("aria-busy", "true");
    expect(screen.getAllByTestId("data-table-skeleton-row")).toHaveLength(3);
  });
});
