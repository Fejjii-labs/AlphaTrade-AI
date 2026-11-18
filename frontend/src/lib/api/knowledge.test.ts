import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./client";
import { api } from "./index";

vi.mock("./client", async original => ({ ...await original<typeof import("./client")>(), apiFetch: vi.fn() }));
beforeEach(() => { vi.mocked(apiFetch).mockReset(); });

describe("knowledge document delete", () => {
  it("calls the existing DELETE endpoint with the encoded document id", async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined);
    await expect(api.knowledge.deleteDocument("doc/1 2")).resolves.toBeUndefined();
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith("/knowledge/documents/doc%2F1%202", { method: "DELETE" });
  });
});
