// Mic capture worklet: capture at the device's NATIVE rate and downsample to
// 16 kHz here. Opening the context at a forced 16 kHz makes some platforms
// re-clock the whole audio device (audible quality drop in the headset).
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = []; this.len = 0;
    this.ratio = sampleRate / 16000;               // e.g. 48000 -> 3.0
    this.chunkIn = Math.round(2048 * this.ratio);  // ~128ms at 16k output
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    this.buf.push(new Float32Array(ch)); this.len += ch.length;
    if (this.len >= this.chunkIn) {
      const all = new Float32Array(this.len); let o = 0;
      for (const b of this.buf) { all.set(b, o); o += b.length; }
      this.buf = []; this.len = 0;
      const outLen = Math.floor(all.length / this.ratio);
      const out = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {           // linear-interp resample
        const x = i * this.ratio, i0 = Math.floor(x), f = x - i0;
        const a = all[i0], b = all[Math.min(i0 + 1, all.length - 1)];
        out[i] = a + (b - a) * f;
      }
      let sum = 0;
      const pcm = new Int16Array(outLen);
      for (let i = 0; i < outLen; i++) {
        sum += out[i] * out[i];
        pcm[i] = Math.max(-32768, Math.min(32767, out[i] * 32768));
      }
      this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(sum / outLen) },
                            [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor('mic-capture', MicCapture);
