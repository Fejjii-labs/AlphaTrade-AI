import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AutonomousPage from "./page";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user" }, organization: { id: "org" }, membershipRole: "owner" }),
}));
vi.mock("@/components/autonomous/MyBloFinTrades", () => ({
  MyBloFinTrades: () => <p>my trades body</p>,
}));
vi.mock("@/components/autonomous/BotSettings", () => ({
  BotSettings: () => <p>bot body</p>,
}));

afterEach(cleanup);

describe("Bot page", () => {
  it("has two sections: My BloFin trades, then Autonomous trading, and no caps or explanations", () => {
    render(<AutonomousPage />);
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Bot"]);
    const titles = [...document.querySelectorAll("#my-trades, #autonomous")].map((el) => el.querySelector("h2, h3, [class*='title']")?.textContent);
    expect(titles).toEqual(["My BloFin trades", "Autonomous trading"]);
    expect(screen.getByText("my trades body")).toBeInTheDocument();
    expect(screen.getByText("bot body")).toBeInTheDocument();
    for (const gone of [/Trading limits/, /Grade risk/, /Stop-loss band/, /Real trading remains disabled/, /internal paper/i]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
  });
});
