import { describe, expect, it } from "vitest";
import {
  blofinHistoryId,
  isBlofinReconciledJournalTrade,
} from "./journal-blofin-reconciliation";

describe("isBlofinReconciledJournalTrade", () => {
  it("detects manual rows closed via the mirror history ref", () => {
    expect(
      isBlofinReconciledJournalTrade({
        source: "manual",
        external_ref: "blofin-demo-history:h-reconciled",
      }),
    ).toBe(true);
  });

  it("does not treat native mirrored rows as reconciled manual lifecycles", () => {
    expect(
      isBlofinReconciledJournalTrade({
        source: "blofin_demo_manual",
        external_ref: "blofin-demo-history:h-long",
      }),
    ).toBe(false);
  });
});

describe("blofinHistoryId", () => {
  it("extracts the venue history id from the server prefix", () => {
    expect(blofinHistoryId("blofin-demo-history:h-reconciled")).toBe("h-reconciled");
  });
});
