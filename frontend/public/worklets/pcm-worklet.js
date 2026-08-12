const TARGET_SAMPLE_RATE = 16000;

class PcmDownsamplerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputSampleRate = sampleRate;
    this.buffer = [];
    this.sendIntervalSamples = Math.floor(this.inputSampleRate * 0.075);
    this.accumulated = 0;
  }

  downsampleTo16k(input) {
    const ratio = this.inputSampleRate / TARGET_SAMPLE_RATE;
    const outputLength = Math.floor(input.length / ratio);
    const output = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
      output[i] = input[Math.floor(i * ratio)];
    }
    return output;
  }

  floatTo16BitPcm(float32Array) {
    const output = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return output;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    this.buffer.push(Float32Array.from(channel));
    this.accumulated += channel.length;

    if (this.accumulated >= this.sendIntervalSamples) {
      const merged = new Float32Array(this.accumulated);
      let offset = 0;
      for (const chunk of this.buffer) {
        merged.set(chunk, offset);
        offset += chunk.length;
      }

      const downsampled = this.downsampleTo16k(merged);
      const pcm16 = this.floatTo16BitPcm(downsampled);
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);

      this.buffer = [];
      this.accumulated = 0;
    }

    return true;
  }
}

registerProcessor('pcm-downsampler-processor', PcmDownsamplerProcessor);
