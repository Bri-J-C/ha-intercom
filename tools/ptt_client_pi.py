#!/usr/bin/env python3
"""
HA Intercom PTT Client - Raspberry Pi Version

Push-to-talk client with GPIO button support for Raspberry Pi.

Usage:
    python ptt_client_pi.py [--name NAME] [--button-pin PIN] [--led-pin PIN]

Hardware:
    - Connect a push button between GPIO pin and GND
    - Optionally connect an LED (with resistor) to show status
"""

import argparse
import queue
import signal
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
    sys.exit(1)

# Try to import GPIO (optional - falls back to keyboard if not available)
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    print("Note: RPi.GPIO not available, using keyboard input")

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


class PiIntercomClient:
    """PTT intercom client with GPIO support."""

    def __init__(
        self,
        name: str,
        button_pin: int = 17,
        led_pin: int = 27,
        target: Optional[str] = None,
        use_multicast: bool = True,
    ):
        self.name = name
        self.device_id = generate_device_id(name)
        self.target = target
        self.use_multicast = use_multicast
        self.sequence = 0
        self.running = False
        self.transmitting = False

        # GPIO pins
        self.button_pin = button_pin
        self.led_pin = led_pin

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

    def setup_gpio(self):
        """Initialize GPIO for button and LED."""
        if not HAS_GPIO:
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Button with pull-up (active low)
        GPIO.setup(self.button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # LED output
        if self.led_pin >= 0:
            GPIO.setup(self.led_pin, GPIO.OUT)
            GPIO.output(self.led_pin, GPIO.LOW)

        print(f"GPIO initialized: button=GPIO{self.button_pin}, LED=GPIO{self.led_pin}")

    def set_led(self, state: bool):
        """Set LED state."""
        if HAS_GPIO and self.led_pin >= 0:
            GPIO.output(self.led_pin, GPIO.HIGH if state else GPIO.LOW)

    def setup_sockets(self):
        """Initialize network sockets."""
        # Transmit socket
        self.tx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.use_multicast:
            self.tx_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, MULTICAST_TTL)

        # Receive socket
        self.rx_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                opus_data = self.encoder.encode(audio_bytes, FRAME_SIZE)

                packet = AudioPacket(
                    device_id=self.device_id,
                    sequence=self.sequence,
                    opus_data=opus_data,
                )
                self.sequence = (self.sequence + 1) % (2**32)

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
                packet = AudioPacket.unpack(data)

                if packet.device_id == self.device_id:
                    continue

                pcm_data = self.decoder.decode(packet.opus_data, FRAME_SIZE)
                self.rx_queue.put(pcm_data)
                self.packets_received += 1
                self.set_led(True)  # Blink on receive

            except socket.timeout:
                self.set_led(self.transmitting)  # LED follows TX state when idle
            except Exception as e:
                print(f"RX error: {e}", file=sys.stderr)

    def button_loop(self):
        """Thread: monitor GPIO button state."""
        if not HAS_GPIO:
            return

        last_state = True  # Pull-up, so True = not pressed
        while self.running:
            current_state = GPIO.input(self.button_pin)

            # Button pressed (falling edge)
            if last_state and not current_state:
                self.transmitting = True
                self.set_led(True)
                print(">>> TRANSMITTING")

            # Button released (rising edge)
            if not last_state and current_state:
                self.transmitting = False
                self.set_led(False)
                print(">>> STOPPED")

            last_state = current_state
            time.sleep(0.02)  # 20ms debounce

    def start(self):
        """Start the client."""
        self.setup_gpio()
        self.setup_sockets()
        self.running = True

        # Start network threads
        self.tx_thread = threading.Thread(target=self.transmit_loop, daemon=True)
        self.rx_thread = threading.Thread(target=self.receive_loop, daemon=True)
        self.tx_thread.start()
        self.rx_thread.start()

        # Start GPIO thread
        if HAS_GPIO:
            self.button_thread = threading.Thread(target=self.button_loop, daemon=True)
            self.button_thread.start()

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
            if self.use_multicast:
                try:
                    mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
                    self.rx_socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                except Exception:
                    pass
            self.rx_socket.close()

        if HAS_GPIO:
            self.set_led(False)
            GPIO.cleanup()


def main():
    parser = argparse.ArgumentParser(description="HA Intercom PTT Client (Raspberry Pi)")
    parser.add_argument("--name", default=None, help="Client name (default: hostname)")
    parser.add_argument("--button-pin", type=int, default=17, help="GPIO pin for PTT button (BCM)")
    parser.add_argument("--led-pin", type=int, default=27, help="GPIO pin for status LED (BCM)")
    parser.add_argument("--target", help="Target IP for unicast")
    parser.add_argument("--multicast", action="store_true", default=True, help="Use multicast")
    args = parser.parse_args()

    # Default name to hostname
    if args.name is None:
        import socket as sock
        args.name = sock.gethostname()

    print("=" * 50)
    print("HA Intercom PTT Client (Raspberry Pi)")
    print("=" * 50)
    print(f"Name: {args.name}")
    print(f"Button: GPIO{args.button_pin}")
    print(f"LED: GPIO{args.led_pin}")
    print(f"Mode: {'Multicast' if args.multicast else 'Unicast'}")
    if args.target:
        print(f"Target: {args.target}")
    print("-" * 50)
    if HAS_GPIO:
        print("Press the button to talk!")
    else:
        print("GPIO not available - use keyboard (Enter to toggle PTT)")
    print("=" * 50)

    client = PiIntercomClient(
        name=args.name,
        button_pin=args.button_pin,
        led_pin=args.led_pin,
        target=args.target,
        use_multicast=args.multicast,
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\nShutting down...")
        client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        client.start()
        print(f"\nClient started. Device ID: {client.device_id.hex()}")

        if HAS_GPIO:
            # GPIO mode - just wait
            while True:
                time.sleep(1)
        else:
            # Keyboard fallback
            ptt_active = False
            while True:
                try:
                    input()
                    ptt_active = not ptt_active
                    client.transmitting = ptt_active
                    if ptt_active:
                        print(">>> TRANSMITTING")
                    else:
                        print(">>> STOPPED")
                except EOFError:
                    break

    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nStats: TX={client.packets_sent} RX={client.packets_received}")
        client.stop()
        print("Client stopped.")


if __name__ == "__main__":
    main()
