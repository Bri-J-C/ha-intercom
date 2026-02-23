#!/usr/bin/env python3
"""
Simulated intercom test node.

Connects to the HA MQTT broker and can:
  - Register as a discoverable intercom device
  - Send call notifications (triggers hub chime streaming)
  - Stream Opus-encoded audio (tones, silence) via UDP
  - Monitor ESP32 diagnostics logs

Usage:
    python3 tools/test_node.py <command> [args]

Commands:
    call <target>           Send MQTT call + wait for hub chime to stream
    chime <ip> [seconds]    Stream a test tone directly via UDP (bypasses hub)
    silence <ip> [seconds]  Stream silence via UDP
    logs                    Poll diagnostics logs from both devices (once)
    watch                   Continuously poll logs (Ctrl+C to stop)
    status                  Show device status (uptime, heap, etc.)
    stress <target> <n>     Send n calls in rapid succession
"""

import argparse
import json
import math
import re
import socket
import struct
import sys
import time
import urllib.request

import paho.mqtt.client as mqtt

# --- Protocol constants (must match protocol.h) ---
SAMPLE_RATE = 16000
FRAME_SIZE = 320          # samples per 20ms frame
FRAME_DURATION_MS = 20
OPUS_BITRATE = 32000
CHANNELS = 1

MULTICAST_GROUP = "239.255.0.100"
AUDIO_PORT = 5005

DEVICE_ID_LENGTH = 8
HEADER_LENGTH = 13        # 8 + 4 + 1

PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1
PRIORITY_EMERGENCY = 2

# --- Network targets ---
MQTT_HOST = "10.0.0.8"
MQTT_PORT = 1883
MQTT_USER = "homeassistantmqtt"
MQTT_PASS = "MQTTP@ssw0rd!"

DEVICES = {
    "bedroom":   {"name": "Bedroom Intercom", "ip": "10.0.0.15"},
    "intercom2": {"name": "INTERCOM2",        "ip": "10.0.0.14"},
}

# Our fake device identity
TEST_DEVICE_ID = b'\xDE\xAD\xBE\xEF\x00\x01\x02\x03'
TEST_DEVICE_NAME = "TestNode"

# --- Opus encoder (lazy init) ---
_encoder = None

def get_encoder():
    global _encoder
    if _encoder is None:
        import opuslib
        _encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP)
        _encoder.bitrate = OPUS_BITRATE
    return _encoder

def encode_pcm(pcm_bytes):
    """Encode 640 bytes of PCM (320 samples, 20ms) to Opus."""
    return get_encoder().encode(pcm_bytes, FRAME_SIZE)

def generate_tone_frame(freq_hz, frame_index, amplitude=0.5):
    """Generate one 20ms PCM frame of a sine wave."""
    samples = []
    for i in range(FRAME_SIZE):
        t = (frame_index * FRAME_SIZE + i) / SAMPLE_RATE
        sample = int(amplitude * 32767 * math.sin(2 * math.pi * freq_hz * t))
        sample = max(-32768, min(32767, sample))
        samples.append(struct.pack('<h', sample))
    return b''.join(samples)

def generate_silence_frame():
    """Generate one 20ms frame of silence."""
    return b'\x00' * (FRAME_SIZE * 2)

# --- UDP sender ---
def create_tx_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
    return sock

def build_packet(sequence, priority, opus_data):
    """Build an audio packet: device_id(8) + seq(4,BE) + priority(1) + opus."""
    header = TEST_DEVICE_ID + struct.pack('>IB', sequence, priority)
    return header + opus_data

def stream_audio(target_ip, duration_s, freq_hz=800, priority=PRIORITY_HIGH, silent=False):
    """Stream Opus-encoded audio to a target via UDP."""
    sock = create_tx_socket()
    num_frames = int(duration_s * 1000 / FRAME_DURATION_MS)
    seq = 0
    frame_interval = 0.018  # 18ms to match hub behavior

    label = f"silence" if silent else f"{freq_hz}Hz tone"
    print(f"  Streaming {label} -> {target_ip}:{AUDIO_PORT} "
          f"({num_frames} frames, {duration_s:.1f}s, pri={priority})")

    for i in range(num_frames):
        if silent:
            pcm = generate_silence_frame()
        else:
            pcm = generate_tone_frame(freq_hz, i)

        opus_data = encode_pcm(pcm)
        packet = build_packet(seq, priority, opus_data)
        seq += 1

        try:
            sock.sendto(packet, (target_ip, AUDIO_PORT))
        except Exception as e:
            print(f"  Send error at frame {i}: {e}")
            break

        time.sleep(frame_interval)

    sock.close()
    print(f"  Stream complete: {seq} frames sent")
    return seq

# --- MQTT helpers ---
def mqtt_connect():
    """Connect to MQTT broker, return client."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, f"test_node_{int(time.time())}")
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    connected = [False]
    def on_connect(c, ud, flags, rc, props=None):
        connected[0] = (rc == 0)

    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    # Wait for connection
    for _ in range(30):
        if connected[0]:
            break
        time.sleep(0.1)

    if not connected[0]:
        print("ERROR: MQTT connection failed")
        sys.exit(1)

    return client

def send_call(client, target_name, caller=TEST_DEVICE_NAME):
    """Publish a call notification via MQTT."""
    payload = json.dumps({"target": target_name, "caller": caller})
    client.publish("intercom/call", payload)
    print(f"  MQTT call sent: {caller} -> {target_name}")

# --- Diagnostics log fetcher ---
def fetch_logs(ip, max_lines=30):
    """Fetch and parse diagnostics logs from an ESP32."""
    try:
        url = f"http://{ip}/diagnostics"
        req = urllib.request.Request(url, headers={"User-Agent": "test_node"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return [f"(fetch error: {e})"]

    # Extract log lines from HTML
    logs = re.findall(r"<span class='log-time'>\[([^\]]+)\]</span>([^<]+)", html)
    lines = []
    for ts, msg in logs[-max_lines:]:
        lines.append(f"[{ts.strip()}]{msg}")
    return lines

def fetch_status_json(ip):
    """Fetch diagnostics JSON from an ESP32."""
    try:
        url = f"http://{ip}/diagnostics/json"
        req = urllib.request.Request(url, headers={"User-Agent": "test_node"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

def fetch_hub_logs(lines=30):
    """Fetch hub addon logs via SSH."""
    import subprocess
    try:
        result = subprocess.run(
            ["ssh", "root@10.0.0.8", f"ha apps logs local_intercom_hub --lines {lines}"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip().split('\n')
    except Exception as e:
        return [f"(hub log fetch error: {e})"]

# --- Commands ---
def cmd_call(args):
    """Send a call notification (hub streams chime automatically)."""
    dev = resolve_device(args.target)
    print(f"\n=== Sending call to {dev['name']} ({dev['ip']}) ===")

    client = mqtt_connect()
    send_call(client, dev['name'])

    # Wait for hub to stream chime (214 frames * 18ms = ~3.9s)
    print(f"  Waiting {args.wait}s for hub chime + ESP32 processing...")
    time.sleep(args.wait)

    # Pull logs from target
    print(f"\n--- {dev['name']} logs ---")
    for line in fetch_logs(dev['ip'], 40):
        print(line)

    print(f"\n--- Hub logs ---")
    for line in fetch_hub_logs(15):
        print(line)

    client.disconnect()

def cmd_chime(args):
    """Stream a test tone directly via UDP (no MQTT call)."""
    dev = resolve_device(args.target)
    duration = args.duration
    print(f"\n=== Streaming test chime to {dev['name']} ({dev['ip']}) ===")
    stream_audio(dev['ip'], duration, freq_hz=800, priority=PRIORITY_HIGH)

    time.sleep(1.0)
    print(f"\n--- {dev['name']} logs ---")
    for line in fetch_logs(dev['ip'], 30):
        print(line)

def cmd_silence(args):
    """Stream silence via UDP."""
    dev = resolve_device(args.target)
    duration = args.duration
    print(f"\n=== Streaming silence to {dev['name']} ({dev['ip']}) ===")
    stream_audio(dev['ip'], duration, silent=True, priority=PRIORITY_HIGH)

    time.sleep(1.0)
    print(f"\n--- {dev['name']} logs ---")
    for line in fetch_logs(dev['ip'], 30):
        print(line)

def cmd_logs(args):
    """Pull logs from both devices and hub."""
    for key, dev in DEVICES.items():
        print(f"\n--- {dev['name']} ({dev['ip']}) ---")
        for line in fetch_logs(dev['ip'], args.lines):
            print(line)

    print(f"\n--- Hub ---")
    for line in fetch_hub_logs(args.lines):
        print(line)

def cmd_watch(args):
    """Continuously poll logs."""
    # Track last seen line per device to avoid duplicates
    last_seen = {key: "" for key in DEVICES}
    last_hub = ""

    print("Watching logs (Ctrl+C to stop)...\n")
    try:
        while True:
            for key, dev in DEVICES.items():
                lines = fetch_logs(dev['ip'], 20)
                new_lines = []
                found_last = (last_seen[key] == "")
                for line in lines:
                    if found_last:
                        new_lines.append(line)
                    elif line == last_seen[key]:
                        found_last = True
                if new_lines:
                    for line in new_lines:
                        # Color warnings/errors
                        prefix = f"[{key[:4].upper()}]"
                        if ' W (' in line or 'WARN' in line:
                            print(f"\033[33m{prefix} {line}\033[0m")
                        elif ' E (' in line or 'ERROR' in line:
                            print(f"\033[31m{prefix} {line}\033[0m")
                        else:
                            print(f"{prefix} {line}")
                    last_seen[key] = lines[-1] if lines else last_seen[key]

            # Hub logs
            hub_lines = fetch_hub_logs(10)
            new_hub = []
            found_last = (last_hub == "")
            for line in hub_lines:
                if found_last:
                    new_hub.append(line)
                elif line == last_hub:
                    found_last = True
            if new_hub:
                for line in new_hub:
                    print(f"[HUB ] {line}")
                last_hub = hub_lines[-1] if hub_lines else last_hub

            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")

def cmd_status(args):
    """Show device status."""
    for key, dev in DEVICES.items():
        data = fetch_status_json(dev['ip'])
        print(f"\n{dev['name']} ({dev['ip']}):")
        if 'error' in data:
            print(f"  ERROR: {data['error']}")
        else:
            print(f"  Uptime: {data.get('uptime_formatted', '?')}")
            print(f"  Free heap: {data.get('free_heap', '?')}")
            print(f"  Min heap: {data.get('min_heap', '?')}")
            print(f"  Reset: {data.get('reset_reason', '?')}")
            print(f"  TX sent: {data.get('tx_packets_sent', '?')}")
            print(f"  TX failed: {data.get('tx_packets_failed', '?')}")

def cmd_stress(args):
    """Send multiple calls rapidly."""
    dev = resolve_device(args.target)
    n = args.count
    delay = args.delay

    print(f"\n=== Stress test: {n} calls to {dev['name']}, {delay}s apart ===")
    client = mqtt_connect()

    for i in range(n):
        print(f"\n--- Call {i+1}/{n} ---")
        send_call(client, dev['name'])
        time.sleep(delay)

    # Wait for everything to settle
    print(f"\nWaiting 6s for audio to complete...")
    time.sleep(6)

    print(f"\n--- {dev['name']} logs ---")
    for line in fetch_logs(dev['ip'], 50):
        print(line)

    print(f"\n--- Hub logs ---")
    for line in fetch_hub_logs(20):
        print(line)

    client.disconnect()

def cmd_call_and_stream(args):
    """Send MQTT call AND stream our own tone simultaneously (test race)."""
    dev = resolve_device(args.target)
    print(f"\n=== Call + simultaneous stream to {dev['name']} ===")

    client = mqtt_connect()
    send_call(client, dev['name'])

    # Immediately stream our own audio (simulates hub chime arriving fast)
    stream_audio(dev['ip'], args.duration, freq_hz=600, priority=PRIORITY_HIGH)

    time.sleep(1.5)
    print(f"\n--- {dev['name']} logs ---")
    for line in fetch_logs(dev['ip'], 40):
        print(line)

    client.disconnect()

# --- Helpers ---
def resolve_device(name):
    """Resolve a device name/alias to {name, ip}."""
    key = name.lower().replace(" ", "").replace("_", "")
    if key in DEVICES:
        return DEVICES[key]
    # Try matching by full name
    for dev in DEVICES.values():
        if dev['name'].lower().replace(" ", "") == key:
            return dev
    # Try as raw IP
    if '.' in name:
        return {"name": name, "ip": name}
    print(f"Unknown device: {name}")
    print(f"Available: {', '.join(DEVICES.keys())}")
    sys.exit(1)

# --- Main ---
def main():
    parser = argparse.ArgumentParser(description="Intercom test node")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("call", help="Send MQTT call (hub streams chime)")
    p.add_argument("target", help="Device name or alias")
    p.add_argument("--wait", type=float, default=6.0, help="Seconds to wait before pulling logs")

    p = sub.add_parser("chime", help="Stream test tone via UDP")
    p.add_argument("target", help="Device name or alias")
    p.add_argument("duration", type=float, nargs="?", default=4.0, help="Duration in seconds")

    p = sub.add_parser("silence", help="Stream silence via UDP")
    p.add_argument("target", help="Device name or alias")
    p.add_argument("duration", type=float, nargs="?", default=2.0, help="Duration in seconds")

    p = sub.add_parser("logs", help="Pull logs from all devices")
    p.add_argument("--lines", type=int, default=30, help="Number of log lines")

    sub.add_parser("watch", help="Continuously poll logs")

    sub.add_parser("status", help="Show device status")

    p = sub.add_parser("stress", help="Send N calls rapidly")
    p.add_argument("target", help="Device name or alias")
    p.add_argument("count", type=int, nargs="?", default=5, help="Number of calls")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between calls")

    p = sub.add_parser("race", help="Send MQTT call + stream audio simultaneously")
    p.add_argument("target", help="Device name or alias")
    p.add_argument("duration", type=float, nargs="?", default=4.0, help="Stream duration")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cmds = {
        "call": cmd_call,
        "chime": cmd_chime,
        "silence": cmd_silence,
        "logs": cmd_logs,
        "watch": cmd_watch,
        "status": cmd_status,
        "stress": cmd_stress,
        "race": cmd_call_and_stream,
    }

    cmds[args.command](args)

if __name__ == "__main__":
    main()
