import { render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { describe, expect, it } from "vitest";

import { IconTile } from "@/components/ui/icon-tile";

describe("IconTile", () => {
  it("is decorative when it has no label", () => {
    render(<IconTile icon={Inbox} />);
    const tile = screen.getByTestId("icon-tile");
    expect(tile).toHaveAttribute("aria-hidden", "true");
    expect(tile).toHaveAttribute("data-tone", "accent");
    expect(tile.className).toContain("rounded-control");
  });

  it("exposes a name and the paper tone", () => {
    render(<IconTile icon={Inbox} tone="paper" label="Paper simulator" />);
    expect(screen.getByRole("img", { name: "Paper simulator" })).toHaveAttribute("data-tone", "paper");
  });
});
