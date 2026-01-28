/**
 * PTT Audio Processor - AudioWorklet for capturing microphone audio
 *
 * Captures audio in 20ms frames (320 samples at 16kHz) matching
 * the intercom protocol's FRAME_SIZE.
 */
class PTTProcessor extends AudioWorkletProcessor {
    constructor() {
        super();

        // Match intercom protocol settings
        this.frameSize = 320;  // 20ms at 16kHz
        this.buffer = new Float32Array(this.frameSize);
        this.bufferIndex = 0;
        this.transmitting = false;

        // Handle messages from main thread
        this.port.onmessage = (event) => {
            if (event.data.type === 'ptt') {
                this.transmitting = event.data.active;
            }
        };
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];

        // No input or not transmitting - return silence tracking
        if (!input || !input[0] || !this.transmitting) {
            return true;
        }

        const channel = input[0];

        // Accumulate samples into frame buffer
        for (let i = 0; i < channel.length; i++) {
            this.buffer[this.bufferIndex++] = channel[i];

            // When we have a full frame, send it
            if (this.bufferIndex >= this.frameSize) {
                // Convert Float32 (-1.0 to 1.0) to Int16 (-32768 to 32767)
                const int16 = new Int16Array(this.frameSize);
                for (let j = 0; j < this.frameSize; j++) {
                    // Clamp and convert
                    const sample = Math.max(-1, Math.min(1, this.buffer[j]));
                    int16[j] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
                }

                // Send to main thread (transfer buffer for efficiency)
                this.port.postMessage(
                    { type: 'audio', pcm: int16.buffer },
                    [int16.buffer]
                );

                // Reset buffer
                this.buffer = new Float32Array(this.frameSize);
                this.bufferIndex = 0;
            }
        }

        return true;
    }
}

registerProcessor('ptt-processor', PTTProcessor);
