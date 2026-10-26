import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import JournalImportPage from "./page";
import { api } from "@/lib/api";
import type { JournalImportResult } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

vi.mock("@/lib/api", () => ({
  api: {
    journal: {
      importTrades: vi.fn(),
      listImports: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 }),
      getImport: vi.fn(),
    },
  },
}));

const importTrades = vi.mocked(api.journal.importTrades);
const listImports = vi.mocked(api.journal.listImports);

const CSV = "symbol,side,entry_price,pnl,trade_id\nBTCUSDT,buy,64500,496.8,ex-1\nETHUSDT,sell,3000,-50,ex-2";

function dryRunResult(overrides: Partial<JournalImportResult> = {}): JournalImportResult {
  return {
    mode: "dry_run",
    committed: false,
    batch_id: null,
    total_rows: 2,
    created_count: 2,
    duplicate_count: 0,
    invalid_count: 0,
    results: [
      { index: 0, outcome: "would_create", external_ref: "ex-1", journal_trade_id: null, errors: [] },
      { index: 1, outcome: "would_create", external_ref: "ex-2", journal_trade_id: null, errors: [] },
    ],
    ...overrides,
  };
}

async function pasteCsvAndPreview(csv: string = CSV) {
  render(<JournalImportPage />);
  fireEvent.change(screen.getByLabelText("Or paste CSV text"), { target: { value: csv } });
  await screen.findByText(/Parsed 2 data row\(s\)/);
  fireEvent.click(screen.getByRole("button", { name: "Preview (dry-run)" }));
}

describe("JournalImportPage (AT-033)", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    listImports.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  });

  it("renders heading and empty import history", async () => {
    render(<JournalImportPage />);
    expect(screen.getByText("Journal import")).toBeInTheDocument();
    expect(await screen.findByText("No imports yet")).toBeInTheDocument();
  });

  it("parses pasted CSV and auto-detects column mapping", async () => {
    render(<JournalImportPage />);
    fireEvent.change(screen.getByLabelText("Or paste CSV text"), { target: { value: CSV } });
    expect(await screen.findByText(/Parsed 2 data row\(s\) with 5 column\(s\)/)).toBeInTheDocument();
    expect((screen.getByLabelText("Symbol *") as HTMLSelectElement).value).toBe("0");
    expect((screen.getByLabelText("Direction (long/short) *") as HTMLSelectElement).value).toBe("1");
    expect((screen.getByLabelText("Net PnL") as HTMLSelectElement).value).toBe("3");
    expect((screen.getByLabelText("External ref (dedup id)") as HTMLSelectElement).value).toBe("4");
  });

  it("runs a dry-run and renders mixed reconciliation outcomes", async () => {
    importTrades.mockResolvedValue(
      dryRunResult({
        created_count: 1,
        duplicate_count: 1,
        invalid_count: 0,
        results: [
          { index: 0, outcome: "would_create", external_ref: "ex-1", journal_trade_id: null, errors: [] },
          {
            index: 1,
            outcome: "duplicate",
            external_ref: "ex-2",
            journal_trade_id: "11111111-2222-3333-4444-555555555555",
            errors: [],
          },
        ],
      }),
    );
    await pasteCsvAndPreview();
    expect(await screen.findByText("duplicate (skipped)")).toBeInTheDocument();
    expect(importTrades).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: "dry_run",
        rows: [
          expect.objectContaining({
            symbol: "BTCUSDT",
            direction: "long",
            net_pnl: "496.8",
            external_ref: "ex-1",
          }),
          expect.objectContaining({ symbol: "ETHUSDT", direction: "short" }),
        ],
      }),
    );
    expect(screen.getByText("Already in your journal")).toBeInTheDocument();
  });

  it("disables commit while the dry-run reports invalid rows", async () => {
    importTrades.mockResolvedValue(
      dryRunResult({
        created_count: 1,
        invalid_count: 1,
        results: [
          { index: 0, outcome: "would_create", external_ref: "ex-1", journal_trade_id: null, errors: [] },
          { index: 1, outcome: "invalid", external_ref: null, journal_trade_id: null, errors: ["symbol: bad"] },
        ],
      }),
    );
    await pasteCsvAndPreview();
    const commitButton = await screen.findByRole("button", { name: /Commit 1 row\(s\)/ });
    expect(commitButton).toBeDisabled();
    expect(
      screen.getByText(/Commits are all-or-nothing: nothing is written while any row is invalid/),
    ).toBeInTheDocument();
  });

  it("commits after a clean dry-run and shows the committed batch", async () => {
    importTrades
      .mockResolvedValueOnce(dryRunResult())
      .mockResolvedValueOnce(
        dryRunResult({
          mode: "commit",
          committed: true,
          batch_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
          results: [
            { index: 0, outcome: "created", external_ref: "ex-1", journal_trade_id: "id-1", errors: [] },
            { index: 1, outcome: "created", external_ref: "ex-2", journal_trade_id: "id-2", errors: [] },
          ],
        }),
      );
    await pasteCsvAndPreview();
    const commitButton = await screen.findByRole("button", { name: /Commit 2 row\(s\)/ });
    expect(commitButton).toBeEnabled();
    fireEvent.click(commitButton);
    expect(await screen.findByText(/Import committed — created/)).toBeInTheDocument();
    expect(importTrades).toHaveBeenLastCalledWith(expect.objectContaining({ mode: "commit" }));
    expectNoIdentifiers();
  });

  it("surfaces a commit failure and explains that re-running is safe", async () => {
    importTrades
      .mockResolvedValueOnce(dryRunResult())
      .mockRejectedValueOnce(new Error("Server exploded"));
    await pasteCsvAndPreview();
    fireEvent.click(await screen.findByRole("button", { name: /Commit 2 row\(s\)/ }));
    await waitFor(() =>
      expect(
        screen.getByText(/Server exploded — nothing was written\. Re-running the import is safe/),
      ).toBeInTheDocument(),
    );
  });

  it("blocks the preview when required columns are unmapped", async () => {
    render(<JournalImportPage />);
    fireEvent.change(screen.getByLabelText("Or paste CSV text"), {
      target: { value: "foo,bar\n1,2\n3,4" },
    });
    await screen.findByText(/Parsed 2 data row\(s\)/);
    expect(screen.getByText("Map a column to Symbol.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview (dry-run)" })).toBeDisabled();
    expect(importTrades).not.toHaveBeenCalled();
  });

  it("renders committed batches in the import history", async () => {
    listImports.mockResolvedValue({
      items: [
        {
          id: "99999999-8888-7777-6666-555555555555",
          organization_id: "org",
          user_id: "user",
          status: "committed",
          source_label: "june-export.csv",
          total_rows: 10,
          created_count: 8,
          duplicate_count: 2,
          invalid_count: 0,
          row_report: [],
          created_at: "2026-07-20T10:00:00Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    render(<JournalImportPage />);
    expect(await screen.findByText("june-export.csv")).toBeInTheDocument();
    expect(screen.getByText(/8 created, 2 duplicates of 10 rows/)).toBeInTheDocument();
    expect(screen.getByText("committed")).toBeInTheDocument();
  });
});
