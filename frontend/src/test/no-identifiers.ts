import { expect } from "vitest";

// UUIDs, long hex digests, and the truncated "1a2b3c4d…" form used to leak IDs.
const IDENTIFIER_PATTERNS = [
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i,
  /\b[0-9a-f]{32,}\b/i,
  /\b[0-9a-z]{6,}…/i,
];

/** Raw provenance may only render inside collapsed `TechnicalDetails` sections. */
export function mainViewText(root: HTMLElement = document.body): string {
  const copy = root.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("[data-technical-details]").forEach((node) => node.remove());
  return copy.textContent ?? "";
}

export function expectNoIdentifiers(root: HTMLElement = document.body) {
  const text = mainViewText(root);
  for (const pattern of IDENTIFIER_PATTERNS) {
    expect(text).not.toMatch(pattern);
  }
}
