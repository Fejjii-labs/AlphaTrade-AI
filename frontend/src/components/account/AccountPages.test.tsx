import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmailVerificationNotice } from "@/components/account/EmailVerificationNotice";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "trader@example.com", email_verified: false },
    refreshProfile: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: { auth: { requestVerifyEmail: vi.fn() } },
  ApiError: class ApiError extends Error {},
}));

describe("Account UI", () => {
  it("renders verification notice", () => {
    render(<EmailVerificationNotice />);
    expect(screen.getByText(/Verify your email/i)).toBeInTheDocument();
    expect(screen.getByText(/trader@example.com/)).toBeInTheDocument();
    expect(screen.getByText(/Check your inbox\.$/)).toBeInTheDocument();
    expect(screen.queryByText(/mock provider|local dev|server-side/i)).not.toBeInTheDocument();
  });
});
