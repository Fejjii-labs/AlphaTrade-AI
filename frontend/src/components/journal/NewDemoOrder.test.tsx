import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { NewDemoOrder } from "./NewDemoOrder";

vi.mock("@/components/settings/ManualDemoTest", () => ({
  ManualDemoTest: () => <div>Manual demo flow</div>,
}));

afterEach(cleanup);

it("opens the existing manual demo flow in a sheet and closes it again", () => {
  render(<NewDemoOrder />);
  expect(screen.queryByText("Manual demo flow")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "New demo order" }));
  const dialog = screen.getByRole("dialog", { name: "New demo order" });
  expect(dialog).toHaveTextContent("Manual demo flow");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
