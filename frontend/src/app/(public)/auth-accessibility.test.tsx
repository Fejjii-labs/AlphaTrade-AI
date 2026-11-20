import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ForgotPasswordPage from "@/app/(public)/forgot-password/page";
import LoginPage from "@/app/(public)/login/page";
import RegisterPage from "@/app/(public)/register/page";
import ResetPasswordPage from "@/app/(public)/reset-password/page";

const login = vi.fn();
const register = vi.fn();
const requestPasswordReset = vi.fn();
const confirmPasswordReset = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("token=reset-token"),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ login, register }),
  getAuthErrorMessage: () => "Invalid email or password.",
}));

vi.mock("@/lib/api", () => ({
  api: {
    auth: {
      requestPasswordReset: (...args: unknown[]) => requestPasswordReset(...args),
      confirmPasswordReset: (...args: unknown[]) => confirmPasswordReset(...args),
    },
  },
  ApiError: class ApiError extends Error {},
}));

/**
 * FP2-112 — auth forms must expose autocomplete hints for password managers and
 * mobile keyboards, and must associate submit errors with the fields they
 * invalidate.
 */
describe("auth form accessibility (FP2-112)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("labels login fields for password-manager autofill", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
  });

  it("marks login fields invalid and points them at the error on failure", async () => {
    login.mockRejectedValueOnce(new Error("nope"));
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "t@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const errorId = screen.getByRole("alert").id;
    expect(errorId).toBeTruthy();
    for (const field of ["Email", "Password"]) {
      const input = screen.getByLabelText(field);
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(input.getAttribute("aria-describedby")).toContain(errorId);
    }
  });

  it("has no invalid or described-by state before submission", () => {
    render(<LoginPage />);
    const email = screen.getByLabelText("Email");
    expect(email).not.toHaveAttribute("aria-invalid");
    expect(email).not.toHaveAttribute("aria-describedby");
  });

  it("uses new-password and organization autocomplete on register", () => {
    render(<RegisterPage />);
    expect(screen.getByLabelText("Organization")).toHaveAttribute("autocomplete", "organization");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(password.getAttribute("aria-describedby")).toBe("register-password-hint");
    expect(screen.getByText("Use at least 12 characters.")).toHaveAttribute(
      "id",
      "register-password-hint",
    );
  });

  it("keeps the register password hint associated alongside a submit error", async () => {
    register.mockRejectedValueOnce(new Error("nope"));
    render(<RegisterPage />);

    fireEvent.submit(screen.getByRole("button", { name: "Create account" }).closest("form")!);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    const describedBy = screen.getByLabelText("Password").getAttribute("aria-describedby");
    expect(describedBy).toContain("register-password-hint");
    expect(describedBy).toContain("register-error");
  });

  it("uses username autocomplete on forgot password", () => {
    render(<ForgotPasswordPage />);
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
  });

  it("uses new-password autocomplete and an associated hint on reset password", () => {
    render(<ResetPasswordPage />);
    const password = screen.getByLabelText("New password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(password.getAttribute("aria-describedby")).toBe("reset-password-hint");
  });
});
