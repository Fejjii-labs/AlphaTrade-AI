import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { ProviderFreshness } from "./ProviderFreshness";

afterEach(cleanup);

const base = { generated_at: "2026-10-29T10:00:00Z", providers: [] };

it("shows last-checked time and age for a fresh cache", () => {
  render(<ProviderFreshness providers={{ ...base, checked_at: "2026-10-29T09:59:50Z", age_seconds: 10, stale: false }} />);
  const el = screen.getByTestId("provider-freshness");
  expect(el).toHaveAttribute("data-stale", "false");
  expect(el).toHaveTextContent("Last checked");
  expect(el).toHaveTextContent("(10s ago)");
});

it("flags a stale cache as unknown", () => {
  render(<ProviderFreshness providers={{ ...base, checked_at: "2026-10-29T09:00:00Z", age_seconds: 3600, stale: true }} />);
  const el = screen.getByTestId("provider-freshness");
  expect(el).toHaveAttribute("data-stale", "true");
  expect(el).toHaveTextContent(/status unknown/i);
});

it("treats a missing check time as unknown", () => {
  render(<ProviderFreshness providers={{ ...base }} />);
  expect(screen.getByTestId("provider-freshness")).toHaveTextContent(/never completed/);
});
