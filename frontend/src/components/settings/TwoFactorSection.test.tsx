import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TwoFactorSection } from "./TwoFactorSection";

const twoFactor = vi.hoisted(() => ({
  status: vi.fn(),
  setup: vi.fn(),
  confirm: vi.fn(),
  disable: vi.fn(),
  regenerateRecoveryCodes: vi.fn(),
  revokeDevices: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: { auth: { twoFactor } } }));

const off = { enabled: false, pending_setup: false, recovery_codes_remaining: 0, remembered_devices: 0 };
const on = { enabled: true, pending_setup: false, recovery_codes_remaining: 10, remembered_devices: 1 };
const codes = Array.from({ length: 10 }, (_, i) => `abcd${i}-efgh${i}`);

describe("TwoFactorSection", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("enrols with QR, manual key and a confirm code, then shows recovery codes once", async () => {
    twoFactor.status.mockResolvedValueOnce(off).mockResolvedValueOnce(on);
    twoFactor.setup.mockResolvedValueOnce({
      manual_key: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
      otpauth_uri: "otpauth://totp/x",
      qr_svg_data_uri: "data:image/svg+xml;base64,PHN2Zy8+",
      digits: 6,
      period_seconds: 30,
    });
    twoFactor.confirm.mockResolvedValueOnce({ recovery_codes: codes });
    render(<TwoFactorSection />);

    fireEvent.click(await screen.findByRole("button", { name: "Turn on two-factor sign-in" }));
    expect(await screen.findByAltText("QR code for your authenticator app")).toHaveAttribute(
      "src",
      "data:image/svg+xml;base64,PHN2Zy8+",
    );
    expect(screen.getByTestId("two-factor-manual-key")).toHaveTextContent("JBSW Y3DP");

    fireEvent.change(screen.getByLabelText("Code from the app"), { target: { value: "123456" } });
    fireEvent.submit(screen.getByRole("button", { name: "Turn on" }).closest("form")!);

    await waitFor(() => expect(twoFactor.confirm).toHaveBeenCalledWith("123456"));
    expect(await screen.findByTestId("recovery-codes")).toHaveTextContent(codes[0]);
    expect(await screen.findByText("On")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "I saved them" }));
    expect(screen.queryByTestId("recovery-codes")).not.toBeInTheDocument();
  });

  it("needs a current code to turn off and shows a wrong-code error", async () => {
    twoFactor.status.mockResolvedValue(on);
    twoFactor.disable.mockRejectedValueOnce(new Error("Invalid authentication code."));
    render(<TwoFactorSection />);

    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    fireEvent.change(screen.getByLabelText("Current authenticator code"), { target: { value: "000000" } });
    fireEvent.submit(screen.getByRole("button", { name: "Turn off" }).closest("form")!);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid authentication code."));
    expect(twoFactor.disable).toHaveBeenCalledWith("000000");
  });

  it("forgets remembered devices", async () => {
    twoFactor.status.mockResolvedValue(on);
    twoFactor.revokeDevices.mockResolvedValueOnce({ message: "Forgot 1 remembered device(s)." });
    render(<TwoFactorSection />);

    fireEvent.click(await screen.findByRole("button", { name: "Forget remembered devices" }));
    expect(await screen.findByText("Forgot 1 remembered device(s).")).toBeInTheDocument();
  });
});
