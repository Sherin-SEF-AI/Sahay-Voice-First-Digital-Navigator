/**
 * SAHAY AudioWorklet Processor
 *
 * Captures PCM audio from the microphone, buffers into chunks,
 * and posts Int16 PCM data to the main thread for WebSocket
 * transmission to the Gemini Live API.
 *
 * Expected format: 16kHz mono PCM16 (signed 16-bit integers).
 */

class SahayAudioProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._buffer = new Float32Array(0);
        this._chunkSize = 4096; // samples per chunk
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input[0]) return true;

        const channelData = input[0]; // mono channel

        // Append to buffer
        const newBuffer = new Float32Array(
            this._buffer.length + channelData.length
        );
        newBuffer.set(this._buffer);
        newBuffer.set(channelData, this._buffer.length);
        this._buffer = newBuffer;

        // Send chunks when buffer is large enough
        while (this._buffer.length >= this._chunkSize) {
            const chunk = this._buffer.slice(0, this._chunkSize);
            this._buffer = this._buffer.slice(this._chunkSize);

            // Convert Float32 (-1.0 to 1.0) to Int16 (-32768 to 32767)
            const pcm16 = new Int16Array(chunk.length);
            for (let i = 0; i < chunk.length; i++) {
                const s = Math.max(-1, Math.min(1, chunk[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            this.port.postMessage({ pcmData: pcm16 });
        }

        return true;
    }
}

registerProcessor("sahay-audio-processor", SahayAudioProcessor);
