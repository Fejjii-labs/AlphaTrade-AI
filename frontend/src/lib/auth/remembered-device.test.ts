import { afterEach, describe, expect, it } from "vitest";

import { forgetRememberedDevice, rememberDevice, rememberedDeviceToken } from "./remembered-device";

const future = () => new Date(Date.now() + 86_400_000).toISOString();

describe("remembered-device tokens", () => {
  afterEach(() => localStorage.clear());

  it("stores per normalized email and forgets on request", () => {
    rememberDevice("Trader@Example.com ", "token-a", future());
    expect(rememberedDeviceToken("trader@example.com")).toBe("token-a");
    expect(rememberedDeviceToken("other@example.com")).toBeUndefined();
    forgetRememberedDevice("TRADER@example.com");
    expect(rememberedDeviceToken("trader@example.com")).toBeUndefined();
  });

  it("drops expired tokens", () => {
    rememberDevice("t@example.com", "old", new Date(Date.now() - 1000).toISOString());
    expect(rememberedDeviceToken("t@example.com")).toBeUndefined();
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("alphatrade:2fa-remembered-devices", "{not json");
    expect(rememberedDeviceToken("t@example.com")).toBeUndefined();
  });
});
