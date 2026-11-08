import { randomBytes } from "node:crypto";

/**
 * Password for an account the e2e run registers itself. Generated per call so
 * no shared literal lives in the repository (Fable audit M20). It meets the
 * backend's 12-character minimum and mixes character classes.
 */
export function freshE2EPassword(): string {
  return `E2e-${randomBytes(12).toString("base64url")}-9a`;
}
