import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ForgotPasswordPage from "@/app/(public)/forgot-password/page";

vi.mock("@/lib/api", () => ({
  api: { auth: { requestPasswordReset: vi.fn() } },
  ApiError: class ApiError extends Error {},
}));

describe("ForgotPasswordPage", () => {
  it("renders forgot password form", () => {
    render(<ForgotPasswordPage />);
    expect(screen.getByRole("heading", { name: /Forgot password/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send reset link/i })).toBeInTheDocument();
  });
});
