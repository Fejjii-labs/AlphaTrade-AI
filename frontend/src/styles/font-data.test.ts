import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("tabular trading numerals", () => {
  it("uses a mono stack for font-data (JetBrains Mono)", () => {
    const globals = readFileSync(
      resolve(__dirname, "../app/globals.css"),
      "utf8",
    );
    expect(globals).toContain("font-variant-numeric: tabular-nums");
    expect(globals).toContain("var(--font-mono)");
  });
});
