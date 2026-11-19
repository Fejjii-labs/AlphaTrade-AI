import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ResetPasswordPage from "@/app/(public)/reset-password/page";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("token=test-reset-token-value-xxx"),
}));

vi.mock("@/lib/api", () => ({
  api: { auth: { confirmPasswordReset: vi.fn() } },
  ApiError: class ApiError extends Error {},
}));

describe("ResetPasswordPage", () => {
  it("renders reset password form", async () => {
    render(<ResetPasswordPage />);
    expect(await screen.findByRole("heading", { name: /Reset password/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/New password/i)).toBeInTheDocument();
  });
});
