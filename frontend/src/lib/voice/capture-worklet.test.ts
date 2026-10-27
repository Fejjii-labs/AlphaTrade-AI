import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { buildSecurityHeaders } from "@/lib/security-headers";
import { CAPTURE_WORKLET_URL } from "./realtime-audio";

const root = path.resolve(__dirname, "../../..");

describe("voice capture worklet (UI overhaul B7b)", () => {
  it("is a same-origin static file, not a blob: URL", () => {
    expect(CAPTURE_WORKLET_URL).toBe("/voice/capture-worklet.js");
    const file = readFileSync(path.join(root, "public", CAPTURE_WORKLET_URL), "utf8");
    expect(file).toContain("registerProcessor('alphatrade-voice-capture'");
    const audio = readFileSync(path.join(root, "src/lib/voice/realtime-audio.ts"), "utf8");
    expect(audio).not.toContain("createObjectURL");
  });

  it("loads under the production CSP without allowing blob: scripts", () => {
    const csp = buildSecurityHeaders({ apiOrigin: "https://api.example.test", dev: false })
      .find((header) => header.key === "Content-Security-Policy")!.value;
    const scriptSrc = csp.split(";").map((part) => part.trim()).find((part) => part.startsWith("script-src"))!;
    expect(scriptSrc).toContain("'self'");
    expect(scriptSrc).not.toContain("blob:");
  });
});
