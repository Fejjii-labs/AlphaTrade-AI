import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PaperModeBanner } from "@/components/PaperModeBanner";
import type { SafetyPosture } from "@/contexts/AppContext";
import LoginPage from "./page";

const { posture } = vi.hoisted(() => ({
  posture: {
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "fallback",
    postureKnown: true,
  } as SafetyPosture,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ login: vi.fn() }),
}));

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => posture,
  useAppContext: () => ({
    health: { status: "ok" },
    providers: {
      providers: [
        { kind: "market_data", name: "binance-public" },
        { kind: "llm", name: "openai-llm" },
        { kind: "embeddings", name: "openai-embeddings" },
        { kind: "vector", name: "qdrant" },
        { kind: "tracing", name: "redis", health: "healthy" },
      ],
    },
  }),
}));

beforeEach(() => {
  Object.assign(posture, {
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "fallback",
    postureKnown: true,
  });
});

describe("login presentation", () => {
  it("shows the compact paper indicator and sign-in form without diagnostics", () => {
    const { container } = render(<LoginPage />);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Paper mode active")).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-shield-check")).toBeInTheDocument();
    expect(screen.getByText("Real trading disabled")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.getByText(/Need an account/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Register" })).toHaveAttribute("href", "/register");
    expect(screen.queryByText("PAPER")).not.toBeInTheDocument();
    for (const text of [
      /Paper-only trading workspace/,
      /Provider mode:/,
      /No real exchange orders are placed/,
      /Market:/,
      /LLM:/,
      /Embeddings:/,
      /Qdrant:/,
      /Redis:/,
      /API ok/,
    ]) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
  });

  it("preserves the full banner by default for other pages", () => {
    render(<PaperModeBanner />);
    for (const text of ["PAPER", "Provider mode: fallback", "Market: binance-public", "LLM: openai-llm", "Embeddings: openai-embeddings", "Qdrant: qdrant", "Redis: healthy", "API ok"]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByText(/No real exchange orders are placed/)).toBeInTheDocument();
  });

  it("does not claim paper mode before backend health is verified", () => {
    posture.postureKnown = false;
    render(<LoginPage />);
    expect(screen.getByText("Execution mode unverified")).toBeInTheDocument();
    expect(screen.queryByText("Paper mode active")).not.toBeInTheDocument();
    expect(screen.queryByText("Real trading disabled")).not.toBeInTheDocument();
  });

  it.each([
    { executionMode: "paper", realTradingEnabled: true },
    { executionMode: "unknown", realTradingEnabled: false },
  ])("preserves the warning for unconfirmed posture: %o", (state) => {
    Object.assign(posture, state);
    render(<LoginPage />);
    expect(screen.getByText("Paper-only posture not confirmed")).toBeInTheDocument();
    expect(screen.queryByText("Paper mode active")).not.toBeInTheDocument();
  });
});
