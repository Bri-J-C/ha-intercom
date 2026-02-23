#!/usr/bin/env python3
"""Generate default chime WAV files for the intercom hub.

Creates 16kHz mono 16-bit PCM WAV files in intercom_hub/chimes/.
Re-run to regenerate if you want to tweak the sounds.

Usage:
    python3 tools/generate_chimes.py
"""

import math
import struct
import wave
import os

SAMPLE_RATE = 16000
CHIMES_DIR = os.path.join(os.path.dirname(__file__), '..', 'intercom_hub', 'chimes')


def generate_tone(freq, duration, volume=0.8, fade_ms=10):
    """Generate a sine wave tone with fade-in/fade-out."""
    n_samples = int(SAMPLE_RATE * duration)
    fade_samples = int(SAMPLE_RATE * fade_ms / 1000)
    samples = []
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        sample = math.sin(2 * math.pi * freq * t) * volume
        # Fade in
        if i < fade_samples:
            sample *= i / fade_samples
        # Fade out
        if i > n_samples - fade_samples:
            sample *= (n_samples - i) / fade_samples
        samples.append(sample)
    return samples


def generate_silence(duration):
    """Generate silence."""
    return [0.0] * int(SAMPLE_RATE * duration)


def samples_to_pcm(samples):
    """Convert float samples [-1, 1] to 16-bit PCM bytes."""
    pcm = bytearray()
    for s in samples:
        clamped = max(-1.0, min(1.0, s))
        pcm.extend(struct.pack('<h', int(clamped * 32767)))
    return bytes(pcm)


def write_wav(filename, samples):
    """Write samples to a WAV file."""
    pcm = samples_to_pcm(samples)
    filepath = os.path.join(CHIMES_DIR, filename)
    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    duration = len(samples) / SAMPLE_RATE
    print(f"  {filename}: {duration:.2f}s, {len(pcm)} bytes")


def generate_alert():
    """Two-tone ascending alert — attention-grabbing."""
    samples = []
    # Two ascending tones
    samples.extend(generate_tone(880, 0.2, 0.7))
    samples.extend(generate_silence(0.05))
    samples.extend(generate_tone(1175, 0.3, 0.7))  # D6
    samples.extend(generate_silence(0.1))
    # Repeat
    samples.extend(generate_tone(880, 0.2, 0.7))
    samples.extend(generate_silence(0.05))
    samples.extend(generate_tone(1175, 0.3, 0.7))
    return samples


def generate_gentle():
    """Soft descending melody — non-intrusive."""
    samples = []
    notes = [
        (784, 0.25),   # G5
        (659, 0.25),   # E5
        (523, 0.35),   # C5
    ]
    for freq, dur in notes:
        samples.extend(generate_tone(freq, dur, 0.5, fade_ms=20))
        samples.extend(generate_silence(0.08))
    return samples


def generate_urgent():
    """Rapid triple beep — urgency without being a full alarm."""
    samples = []
    for _ in range(3):
        samples.extend(generate_tone(1000, 0.12, 0.8))
        samples.extend(generate_silence(0.08))
    samples.extend(generate_silence(0.15))
    # Second round slightly higher
    for _ in range(3):
        samples.extend(generate_tone(1200, 0.12, 0.8))
        samples.extend(generate_silence(0.08))
    return samples


def main():
    os.makedirs(CHIMES_DIR, exist_ok=True)
    print(f"Generating chimes in {os.path.abspath(CHIMES_DIR)}/")

    write_wav('alert.wav', generate_alert())
    write_wav('gentle.wav', generate_gentle())
    write_wav('urgent.wav', generate_urgent())

    print("Done.")


if __name__ == '__main__':
    main()
