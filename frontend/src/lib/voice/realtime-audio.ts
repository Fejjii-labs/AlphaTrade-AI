/** PCM24k Web Audio capture/playback; no browser speech service or encoded upload. */
export function pcm24(samples: Float32Array, sampleRate: number): Uint8Array {
  const count = Math.floor(samples.length * 24000 / sampleRate);
  const bytes = new Uint8Array(count * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < count; i++) {
    const position = i * sampleRate / 24000;
    const left = Math.floor(position);
    const weight = position - left;
    const value = Math.max(-1, Math.min(1,
      samples[left] * (1 - weight) + (samples[Math.min(left + 1, samples.length - 1)] ?? 0) * weight));
    view.setInt16(i * 2, Math.round(value < 0 ? value * 32768 : value * 32767), true);
  }
  return bytes;
}

export function encodeAudio(bytes: Uint8Array): string {
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary);
}

export function decodeAudio(encoded: string): Float32Array {
  const binary = atob(encoded);
  if (binary.length % 2 || binary.length > 65536) throw new Error("Invalid voice audio.");
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  return Float32Array.from({ length: bytes.length / 2 }, (_, i) => view.getInt16(i * 2, true) / 32768);
}

const processor = `class AlphaTradeCapture extends AudioWorkletProcessor {
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
registerProcessor('alphatrade-voice-capture', AlphaTradeCapture);`;

export class RealtimeAudio {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private capture: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private silence: GainNode | null = null;
  private outputs = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private item: { id: string; start: number; duration: number } | null = null;
  private generation = 0;

  async start(onAudio: (audio: string) => void): Promise<void> {
    const generation = ++this.generation;
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined")
      throw new Error("Voice is unavailable on this device. Continue typing.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
    } });
    if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
    this.stream = stream;
    const context = new AudioContext({ sampleRate: 24000 });
    this.context = context;
    const url = URL.createObjectURL(new Blob([processor], { type: "application/javascript" }));
    try { await context.audioWorklet.addModule(url); }
    finally { URL.revokeObjectURL(url); }
    if (generation !== this.generation) return;
    await context.resume();
    this.capture = new AudioWorkletNode(context, "alphatrade-voice-capture");
    this.capture.port.onmessage = (event: MessageEvent<Float32Array>) => {
      if (generation === this.generation) onAudio(encodeAudio(pcm24(event.data, context.sampleRate)));
    };
    this.source = context.createMediaStreamSource(stream);
    this.silence = context.createGain();
    this.silence.gain.value = 0;
    this.source.connect(this.capture);
    this.capture.connect(this.silence);
    this.silence.connect(context.destination);
  }

  play(audio: string, itemId: string): void {
    const context = this.context;
    if (!context) return;
    const samples = decodeAudio(audio);
    const buffer = context.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(new Float32Array(samples), 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const start = Math.max(context.currentTime + 0.015, this.nextStart);
    if (this.item?.id !== itemId) this.item = { id: itemId, start, duration: 0 };
    this.item.duration += buffer.duration;
    this.nextStart = start + buffer.duration;
    this.outputs.add(source);
    source.onended = () => { this.outputs.delete(source); source.disconnect(); };
    source.start(start);
  }

  interrupt(): { item_id: string; audio_end_ms: number } | null {
    const position = this.item && this.context ? {
      item_id: this.item.id,
      audio_end_ms: Math.floor(Math.max(0, Math.min(this.item.duration, this.context.currentTime - this.item.start)) * 1000),
    } : null;
    for (const source of this.outputs) { source.stop(); source.disconnect(); }
    this.outputs.clear();
    this.nextStart = this.context?.currentTime ?? 0;
    this.item = null;
    return position;
  }

  stop(): void {
    ++this.generation;
    this.interrupt();
    this.capture?.disconnect();
    if (this.capture) this.capture.port.onmessage = null;
    this.source?.disconnect(); this.silence?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    if (this.context) void this.context.close();
    this.capture = null; this.source = null; this.silence = null;
    this.context = null; this.stream = null;
  }
}
