#!/usr/bin/env python3
"""
HA Intercom PTT Test Client

A push-to-talk client for testing the intercom protocol.
Run two instances to test communication.

Usage:
    python ptt_client.py [--name NAME] [--target IP] [--multicast]

Controls:
    Enter: Toggle PTT (press to start, press again to stop)
    q + Enter: Quit
"""

import argparse
import json
import queue
import socket
import struct
import sys
import threading
import time
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("Error: sounddevice not installed. Run: pip install sounddevice")
    sys.exit(1)

try:
    import opuslib
except ImportError:
    print("Error: opuslib not installed. Run: pip install opuslib")
    print("Note: You may need to install libopus-dev first:")
    print("  Ubuntu/Debian: sudo apt install libopus-dev")
    print("  macOS: brew install opus")
    sys.exit(1)

from protocol import (
    AUDIO_PORT,
    CONTROL_PORT,
    FRAME_DURATION_MS,
    FRAME_SIZE,
    MULTICAST_GROUP,
    MULTICAST_TTL,
    OPUS_BITRATE,
    SAMPLE_RATE,
    AudioPacket,
    generate_device_id,
)


class IntercomClient:
    """PTT intercom client."""

    def __init__(self, name: str, target: Optional[str] = None, use_multicast: bool = True):
        self.name = name
        self.device_id = generate_device_id(name)
        self.target = target
        self.use_multicast = use_multicast
        self.sequence = 0
        self.running = False
        self.transmitting = False

        # Audio queues
        self.tx_queue: queue.Queue = queue.Queue()
        self.rx_queue: queue.Queue = queue.Queue()

        # Opus codec
        self.encoder = opuslib.Encoder(SAMPLE_RATE, 1, opuslib.APPLICATION_VOIP)
        self.encoder.bitrate = OPUS_BITRATE
        self.decoder = opuslib.Decoder(SAMPLE_RATE, 1)

        # Sockets
        self.tx_socket: Optional[socket.socket] = None
        self.rx_socket: Optional[socket.socket] = None

        # Stats
        self.packets_sent = 0
        self.packets_received = 0

    def setup_sockets(self):
        """Initialize network sockets."""
        # Transmit socket
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.use_multicast:
            self.tx_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)

        # Receive socket
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Bind to audio port
        self.rx_socket.bind(("", AUDIO_PORT))

        # Join multicast group
        if self.use_multicast:
            mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
            self.rx_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self.rx_socket.settimeout(0.1)

    def audio_input_callback(self, indata, frames, time_info, status):
        """Callback for audio input (microphone)."""
        if status:
            print(f"Input status: {status}", file=sys.stderr)
        if self.transmitting:
            # Convert to int16 and queue
            audio_data = (indata[:, 0] * 32767).astype(np.int16)
            self.tx_queue.put(audio_data.tobytes())

    def audio_output_callback(self, outdata, frames, time_info, status):
        """Callback for audio output (speaker)."""
        if status:
            print(f"Output status: {status}", file=sys.stderr)
        try:
            data = self.rx_queue.get_nowait()
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
            if len(audio) < frames:
                audio = np.pad(audio, (0, frames - len(audio)))
            outdata[:, 0] = audio[:frames]
        except queue.Empty:
            outdata.fill(0)

    def transmit_loop(self):
        """Thread: encode and send audio packets."""
        while self.running:
            try:
                audio_bytes = self.tx_queue.get(timeout=0.1)

                # Encode with Opus
                opus_data = self.encoder.encode(audio_bytes, FRAME_SIZE)

                # Create and send packet
                packet = AudioPacket(
                    device_id=self.device_id,
                    sequence=self.sequence,
                    opus_data=opus_data,
                )
                self.sequence = (self.sequence + 1) % (2**32)

                # Send to target
                if self.use_multicast or self.target is None:
                    dest = (MULTICAST_GROUP, AUDIO_PORT)
                else:
                    dest = (self.target, AUDIO_PORT)

                self.tx_socket.sendto(packet.pack(), dest)
                self.packets_sent += 1

            except queue.Empty:
                pass
            except Exception as e:
                print(f"TX error: {e}", file=sys.stderr)

    def receive_loop(self):
        """Thread: receive and decode audio packets."""
        while self.running:
            try:
                data, addr = self.rx_socket.recvfrom(4096)

                # Unpack packet
                packet = AudioPacket.unpack(data)

                # Skip our own packets
                if packet.device_id == self.device_id:
                    continue

                # Decode Opus
                pcm_data = self.decoder.decode(packet.opus_data, FRAME_SIZE)
                self.rx_queue.put(pcm_data)
                self.packets_received += 1

            except socket.timeout:
                pass
            except Exception as e:
                print(f"RX error: {e}", file=sys.stderr)

    def start(self):
        """Start the client."""
        self.setup_sockets()
        self.running = True

        # Start network threads
        self.tx_thread = threading.Thread(target=self.transmit_loop, daemon=True)
        self.rx_thread = threading.Thread(target=self.receive_loop, daemon=True)
        self.tx_thread.start()
        self.rx_thread.start()

        # Start audio streams
        self.input_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            blocksize=FRAME_SIZE,
            callback=self.audio_input_callback,
        )
        self.output_stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            blocksize=FRAME_SIZE,
            callback=self.audio_output_callback,
        )
        self.input_stream.start()
        self.output_stream.start()

    def stop(self):
        """Stop the client."""
        self.running = False
        self.transmitting = False

        if hasattr(self, "input_stream"):
            self.input_stream.stop()
            self.input_stream.close()
        if hasattr(self, "output_stream"):
            self.output_stream.stop()
            self.output_stream.close()

        if self.tx_socket:
            self.tx_socket.close()
        if self.rx_socket:
            # Leave multicast group
            if self.use_multicast:
                try:
                    mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
                    self.rx_socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                except Exception:
                    pass
            self.rx_socket.close()

    def set_ptt(self, state: bool):
        """Set PTT state."""
        self.transmitting = state
        if state:
            print(">>> TRANSMITTING - speak now...")
        else:
            print(">>> STOPPED")


def main():
    parser = argparse.ArgumentParser(description="HA Intercom PTT Test Client")
    parser.add_argument("--name", default="client", help="Client name (for device ID)")
    parser.add_argument("--target", help="Target IP for unicast (omit for multicast)")
    parser.add_argument("--multicast", action="store_true", default=True,
                        help="Use multicast (default)")
    parser.add_argument("--unicast", action="store_true",
                        help="Use unicast (requires --target)")
    args = parser.parse_args()

    use_multicast = not args.unicast
    if args.unicast and not args.target:
        print("Error: --unicast requires --target IP")
        sys.exit(1)

    print("=" * 50)
    print("HA Intercom PTT Test Client")
    print("=" * 50)
    print(f"Name: {args.name}")
    print(f"Mode: {'Multicast' if use_multicast else 'Unicast'}")
    if args.target:
        print(f"Target: {args.target}")
    print(f"Audio port: {AUDIO_PORT}")
    print(f"Multicast group: {MULTICAST_GROUP}")
    print("-" * 50)
    print("Controls:")
    print("  Press Enter to toggle PTT (talk/stop)")
    print("  Type 'q' + Enter to quit")
    print("=" * 50)

    client = IntercomClient(
        name=args.name,
        target=args.target,
        use_multicast=use_multicast,
    )

    try:
        client.start()
        print("\nClient started. Listening for audio...")
        print(f"Device ID: {client.device_id.hex()}\n")

        ptt_active = False
        while True:
            try:
                user_input = input()
                if user_input.lower() == "q":
                    break
                # Toggle PTT
                ptt_active = not ptt_active
                client.set_ptt(ptt_active)
            except EOFError:
                break

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        print(f"\nStats: TX={client.packets_sent} RX={client.packets_received}")
        client.stop()
        print("Client stopped.")


if __name__ == "__main__":
    main()
