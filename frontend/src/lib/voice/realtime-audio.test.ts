import { describe, expect, it } from "vitest";
import { decodeAudio, encodeAudio, pcm24 } from "./realtime-audio";

describe("PCM24k protocol (synthetic samples, no microphone acceptance)", () => {
  it("encodes signed little-endian PCM with clipping", () => {
    const values = pcm24(new Float32Array([-2, -1, 0, 1, 2]), 24000);
    const view = new DataView(values.buffer);
    expect(Array.from({ length: 5 }, (_, i) => view.getInt16(i * 2, true))).toEqual([-32768, -32768, 0, 32767, 32767]);
  });
  it("resamples actual device rate to supported 24k", () => {
    expect(pcm24(new Float32Array(2048), 48000)).toHaveLength(2048);
    expect(pcm24(new Float32Array(4410), 44100)).toHaveLength(4800);
  });
  it("round trips PCM24k preserving signed normalized values", () => {
    const values = new Float32Array([-1, -0.5, 0, 0.5, 1]);
    const result = decodeAudio(encodeAudio(pcm24(values, 24000)));
    values.forEach((value, index) => expect(result[index]).toBeCloseTo(value, 4));
  });
  it("refuses malformed or oversized output", () => {
    expect(() => decodeAudio(btoa("x"))).toThrow();
    expect(() => decodeAudio(btoa("x".repeat(65538)))).toThrow();
  });
});
