// The phone's microphone, made into what Pip's ears take (D-149, used by phone.js): the page's sound (usually 48 kHz,
// floats) brought down to 16 kHz 16-bit, sent in 80 ms frames (1280 samples), like the speakerphone's.
// Each output sample is the average of the input samples around it, which keeps out the high sounds that would
// otherwise fold down into speech when the rate is lowered.
class PipCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions || {};
    this.step = sampleRate / (o.rate || 16000);   // input samples per output sample (3 at 48 kHz)
    this.frame = new Int16Array(o.frame || 1280);
    this.n = 0;        // samples in this.frame so far
    this.sum = 0;      // input samples added up for the next output sample…
    this.count = 0;    // …and how many
    this.next = this.step;
    this.pos = 0;      // input samples seen, counting from the last output sample
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      this.sum += input[i];
      this.count++;
      this.pos++;
      if (this.pos >= this.next) {
        const v = Math.max(-1, Math.min(1, this.sum / this.count));
        this.frame[this.n++] = v < 0 ? v * 32768 : v * 32767;
        this.sum = 0; this.count = 0;
        this.next += this.step;
        if (this.n === this.frame.length) {
          this.port.postMessage(this.frame.buffer.slice(0));
          this.n = 0;
        }
      }
    }
    if (this.pos > 1e6) { this.pos -= 1e6; this.next -= 1e6; }   // keep the numbers small
    return true;
  }
}

registerProcessor("pip-capture", PipCapture);
