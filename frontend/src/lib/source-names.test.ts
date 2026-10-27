import { describe, expect, it } from "vitest";
import { distinctSourceNames } from "./source-names";

describe("distinctSourceNames", () => {
  it("keeps distinct titles unchanged", () => {
    expect(distinctSourceNames(["Risk notes", "Entry plan"])).toEqual(["Risk notes", "Entry plan"]);
  });

  it("numbers repeated titles after the first, ignoring case and whitespace", () => {
    expect(distinctSourceNames(["Risk notes", " risk notes ", "Risk notes"])).toEqual([
      "Risk notes",
      "risk notes (2)",
      "Risk notes (3)",
    ]);
  });

  it("names empty or missing titles by position", () => {
    expect(distinctSourceNames(["", null, "Entry plan", "   ", undefined])).toEqual([
      "Source 1",
      "Source 2",
      "Entry plan",
      "Source 4",
      "Source 5",
    ]);
  });

  it("never collides with a real title that looks like a generated name", () => {
    const names = distinctSourceNames(["Source 2", "", "Risk notes (2)", "Risk notes", "Risk notes"]);
    expect(names).toEqual(["Source 2", "Source 2 (2)", "Risk notes (2)", "Risk notes", "Risk notes (3)"]);
    expect(new Set(names.map((name) => name.toLowerCase())).size).toBe(names.length);
  });

  it("keeps a later real title that looks like a generated name exactly as written", () => {
    const names = distinctSourceNames(["Risk notes", "Risk notes", "Risk notes (2)"]);
    expect(names).toEqual(["Risk notes", "Risk notes (3)", "Risk notes (2)"]);
    expect(new Set(names.map((name) => name.toLowerCase())).size).toBe(names.length);
  });

  it("never gives a fallback name that a later real title uses", () => {
    expect(distinctSourceNames(["", "Source 1"])).toEqual(["Source 1 (2)", "Source 1"]);
  });

  it("uses the given fallback label", () => {
    expect(distinctSourceNames([null], "Matching attempt")).toEqual(["Matching attempt 1"]);
  });
});
