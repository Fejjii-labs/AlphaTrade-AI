import { expect } from "vitest";

// UUIDs, long hex digests, and the truncated "1a2b3c4d…" form used to leak IDs.
const IDENTIFIER_PATTERNS = [
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
  /\b[0-9a-f]{32,}\b/i,
  /\b[0-9a-z]{6,}…/i,
];

export function expectNoIdentifiers(root: HTMLElement = document.body) {
  const text = root.textContent ?? "";
  for (const pattern of IDENTIFIER_PATTERNS) {
    expect(text).not.toMatch(pattern);
  }
}
