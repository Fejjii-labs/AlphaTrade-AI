import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/StatusBadge";

describe("StatusBadge", () => {
  it("renders paper mode label", () => {
    render(<StatusBadge label="PAPER mode" tone="paper" />);
    expect(screen.getByText("PAPER mode")).toBeInTheDocument();
  });
});


describe("api client types compile", () => {
  it("exports api helpers", async () => {
    const { api } = await import("@/lib/api");
    expect(api.health.get).toBeTypeOf("function");
    expect(api.providers.status).toBeTypeOf("function");
  });
});
