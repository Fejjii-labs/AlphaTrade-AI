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

function isScannedAttribute(name: string): boolean {
  return name === "title" || name === "alt" || name.startsWith("aria-") || name.startsWith("data-");
}

/**
 * Tooltip, alt, aria-* and data-* values in the main view. Links (`<a>`) and
 * `TechnicalDetails` sections are excluded; hrefs are never scanned.
 */
export function mainViewAttributes(root: HTMLElement = document.body): string[] {
  const values: string[] = [];
  const elements = [root, ...Array.from(root.querySelectorAll("*"))];
  for (const element of elements) {
    if (element.closest("[data-technical-details]") || element.closest("a")) continue;
    for (const attribute of Array.from(element.attributes)) {
      if (isScannedAttribute(attribute.name)) values.push(`${attribute.name}=${attribute.value}`);
    }
  }
  return values;
}

export function expectNoIdentifiers(root: HTMLElement = document.body) {
  const text = mainViewText(root);
  const attributes = mainViewAttributes(root);
  for (const pattern of IDENTIFIER_PATTERNS) {
    expect(text).not.toMatch(pattern);
    expect(attributes.filter((value) => pattern.test(value))).toEqual([]);
  }
}
