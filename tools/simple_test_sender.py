#!/usr/bin/env python3
"""
Simple UDP test sender - sends packets to verify ESP32 is receiving.
No Opus encoding, just raw packets to test network connectivity.
"""

import socket
import struct
import time
import sys

# Protocol constants
MULTICAST_GROUP = "224.0.0.100"
AUDIO_PORT = 5005
DEVICE_ID = b'\x00\x01\x02\x03\x04\x05\x06\x07'

def main():
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

    print(f"Sending test packets to {MULTICAST_GROUP}:{AUDIO_PORT}")
    print("Press Ctrl+C to stop")
    print()

    sequence = 0

    try:
        while True:
            # Build a minimal packet: device_id (8) + sequence (4) + fake opus data
            # The ESP32 will try to decode this and fail, but it should log the RX
            fake_opus = bytes([0xFC, 0xFF, 0xFE] + [0x00] * 20)  # Opus silence-ish frame

            packet = DEVICE_ID + struct.pack('>I', sequence) + fake_opus

            sock.sendto(packet, (MULTICAST_GROUP, AUDIO_PORT))

            if sequence % 50 == 0:
                print(f"Sent packet #{sequence}")

            sequence += 1
            time.sleep(0.02)  # 20ms = 50 packets/sec (matching Opus frame rate)

    except KeyboardInterrupt:
        print(f"\nStopped. Sent {sequence} packets total.")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
