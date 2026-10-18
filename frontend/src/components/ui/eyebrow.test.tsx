import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Eyebrow } from "@/components/ui/eyebrow";
import { formatSectionNumber, SectionHeader } from "@/components/ui/section-header";

describe("Eyebrow", () => {
  it("renders an uppercase highlight label", () => {
    render(<Eyebrow>Overview</Eyebrow>);
    const eyebrow = screen.getByTestId("eyebrow");
    expect(eyebrow.tagName).toBe("P");
    expect(eyebrow).toHaveTextContent("Overview");
    expect(eyebrow.className).toContain("text-eyebrow");
  });

  it("can render as a span inside a heading", () => {
    render(<Eyebrow as="span">01</Eyebrow>);
    expect(screen.getByTestId("eyebrow").tagName).toBe("SPAN");
  });
});

describe("SectionHeader", () => {
  it("pads a numeric section index and keeps actions", () => {
    render(
      <SectionHeader
        eyebrow="Positions"
        number={1}
        title="Open positions"
        actions={<button type="button">All positions</button>}
      />,
    );
    expect(formatSectionNumber(1)).toBe("01");
    expect(screen.getByRole("heading", { level: 2, name: "01 — Open positions" })).toBeInTheDocument();
    expect(screen.getByText("Positions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All positions" })).toBeInTheDocument();
  });

  it("leaves a string index unchanged", () => {
    expect(formatSectionNumber("04")).toBe("04");
  });
});
