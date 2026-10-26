import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Badge } from "./badge";
import { Row, rowBadgeClass } from "./row";

afterEach(cleanup);

function part(name: string) {
  return document.querySelector(`[data-row-part="${name}"]`);
}

describe("Row class contract", () => {
  it("aligns label, value, meta and action on one baseline with a 40px minimum height", () => {
    render(
      <Row
        data-testid="row"
        label="Source"
        value="BTCUSDT"
        meta={<Badge className={rowBadgeClass}>Replay</Badge>}
        action={<button type="button">Up</button>}
      />,
    );
    const row = screen.getByTestId("row");
    expect(row).toHaveClass("grid", "items-baseline", "min-h-10");
    expect(row).toHaveClass("sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_auto_auto]");
    expect(part("meta")).toHaveClass("flex", "items-baseline");
    expect(part("action")).toHaveClass("items-baseline");
    expect(screen.getByText("Replay")).toHaveClass("self-baseline", "leading-none");
  });

  it("sizes the grid to the parts present", () => {
    render(<Row data-testid="row" label="Timezone" value="UTC" />);
    expect(screen.getByTestId("row")).toHaveClass("sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)]");
    expect(part("meta")).toBeNull();
    expect(part("action")).toBeNull();
  });

  it("renders dt/dd inside a description list when definition is set", () => {
    render(
      <dl>
        <Row definition label="Daily loss limit" value="50 USDT" />
      </dl>,
    );
    expect(screen.getByText("Daily loss limit").tagName).toBe("DT");
    expect(screen.getByText("50 USDT").tagName).toBe("DD");
  });

  it("lets a caller override the column template", () => {
    render(<Row data-testid="row" label="Slot 1" className="sm:grid-cols-[3.5rem_1fr]" />);
    const row = screen.getByTestId("row");
    expect(row).toHaveClass("sm:grid-cols-[3.5rem_1fr]");
    expect(row).not.toHaveClass("sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)]");
  });
});
