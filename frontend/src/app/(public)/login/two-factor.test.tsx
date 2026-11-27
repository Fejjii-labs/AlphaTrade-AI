import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/(public)/login/page";

const login = vi.fn();
const completeTwoFactorLogin = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ login, completeTwoFactorLogin }),
  getAuthErrorMessage: (error: unknown) => (error instanceof Error ? error.message : "failed"),
}));

const challenge = {
  two_factor_required: true as const,
  pending_token: "pending-token-value-0123456789",
  expires_in: 300,
  methods: ["totp", "recovery_code"] as Array<"totp" | "recovery_code">,
};

function signIn() {
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "t@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret-password" } });
  fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);
}

describe("login two-step verification", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("stays on the password form when no second factor is needed", async () => {
    login.mockResolvedValueOnce({ status: "signed_in" });
    signIn();
    await waitFor(() => expect(login).toHaveBeenCalled());
    expect(screen.queryByText("Two-step verification")).not.toBeInTheDocument();
  });

  it("asks for the authenticator code and remembers the device on request", async () => {
    login.mockResolvedValueOnce({ status: "two_factor_required", challenge });
    completeTwoFactorLogin.mockResolvedValueOnce(undefined);
    signIn();

    const code = await screen.findByLabelText("Authentication code");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    fireEvent.change(code, { target: { value: "123 456" } });
    fireEvent.click(screen.getByLabelText("Remember this device for 30 days"));
    fireEvent.submit(screen.getByRole("button", { name: "Verify" }).closest("form")!);

    await waitFor(() =>
      expect(completeTwoFactorLogin).toHaveBeenCalledWith(
        "t@example.com",
        challenge.pending_token,
        { code: "123456" },
        true,
        undefined,
      ),
    );
  });

  it("accepts a recovery code instead and shows errors", async () => {
    login.mockResolvedValueOnce({ status: "two_factor_required", challenge });
    completeTwoFactorLogin.mockRejectedValueOnce(new Error("Invalid authentication code."));
    signIn();

    fireEvent.click(await screen.findByRole("button", { name: "Use a recovery code" }));
    fireEvent.change(screen.getByLabelText("Recovery code"), { target: { value: " abcde-fghjk " } });
    fireEvent.submit(screen.getByRole("button", { name: "Verify" }).closest("form")!);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid authentication code."));
    expect(completeTwoFactorLogin).toHaveBeenCalledWith(
      "t@example.com",
      challenge.pending_token,
      { recoveryCode: "abcde-fghjk" },
      false,
      undefined,
    );
    expect(screen.getByLabelText("Recovery code")).toHaveAttribute("aria-describedby", screen.getByRole("alert").id);
  });
});
