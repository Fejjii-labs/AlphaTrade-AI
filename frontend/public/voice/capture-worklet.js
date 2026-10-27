// PCM capture for interactive voice. Served from /public so the AudioWorklet
// loads under the app's own origin (CSP script-src 'self'), not a blob: URL.
class AlphaTradeCapture extends AudioWorkletProcessor {
  constructor() { super(); this.samples = []; }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (input) for (let i = 0; i < input.length; i++) this.samples.push(input[i]);
    if (this.samples.length >= 2048) {
      const chunk = new Float32Array(this.samples.splice(0, 2048));
      this.port.postMessage(chunk, [chunk.buffer]);
    }
    return true;
  }
}
registerProcessor('alphatrade-voice-capture', AlphaTradeCapture);
