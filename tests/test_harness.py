#!/usr/bin/env python3
"""
Test Harness — HA Intercom System
===================================
Shared infrastructure for all test suites:
  - SerialLogMonitor: background capture of ESP32 serial output
  - HubLogMonitor: SSH tail of hub add-on stdout
  - MQTTMonitor: subscribe to intercom/# and capture all messages
  - CrashDetector: uptime snapshot + serial pattern scan
  - LogHarness: orchestrates all monitors with per-test segmentation
  - run_test(): wrapper with crash detection, stale audio check, log dump on FAIL

Requires:
  pip install paho-mqtt pyserial
Optional (for Web PTT tests):
  pip install websockets
"""

import asyncio
import base64
import json
import math
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BEDROOM_IP = "10.0.0.15"
INTERCOM2_IP = "10.0.0.14"
HUB_IP = "10.0.0.8"
HUB_PORT = 8099

MQTT_HOST = "10.0.0.8"
MQTT_PORT = 1883
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

BEDROOM_UNIQUE_ID = "intercom_XXXXXXXX"
INTERCOM2_UNIQUE_ID = "intercom_YYYYYYYY"
HUB_UNIQUE_ID = "intercom_ZZZZZZZZ"

BEDROOM_DEVICE_ID = "XXXXXXXXXXXXXXXX"
INTERCOM2_DEVICE_ID = "YYYYYYYYYYYYYYYY"

CALL_TOPIC = "intercom/call"
HUB_NOTIFY_TOPIC = f"intercom/{HUB_UNIQUE_ID}/notify"

DEVICE_TIMEOUT = 10
HUB_TIMEOUT = 5
DEVICE_USER = os.environ.get("DEVICE_USER", "admin")
DEVICE_PASS = os.environ.get("DEVICE_PASS", "")

PACKETS_PER_SECOND = 50
HEAP_LEAK_THRESHOLD_BYTES = 8192

SERIAL_PORTS = {
    "bedroom": "/dev/ttyACM0",
    "intercom2": "/dev/ttyACM1",
}

DEVICES = {
    "bedroom": {"ip": BEDROOM_IP, "unique_id": BEDROOM_UNIQUE_ID, "device_id": BEDROOM_DEVICE_ID},
    "intercom2": {"ip": INTERCOM2_IP, "unique_id": INTERCOM2_UNIQUE_ID, "device_id": INTERCOM2_DEVICE_ID},
}

# Crash patterns to scan for in serial logs
CRASH_PATTERNS = [
    r"Guru Meditation",
    r"Backtrace:",
    r"panic",
    r"abort\(\)",
    r"ENOMEM",
    r"rst:0x",
    r"Task watchdog",
    r"Interrupt watchdog",
    r"LoadProhibited",
    r"StoreProhibited",
    r"InstrFetchProhibited",
    r"stack overflow",
]
CRASH_RE = re.compile("|".join(CRASH_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
_serial_available = False
try:
    import serial
    _serial_available = True
except ImportError:
    pass

_paho_available = False
try:
    import paho.mqtt.client as mqtt
    _paho_available = True
except ImportError:
    pass

_websockets_available = False
try:
    import websockets
    _websockets_available = True
except ImportError:
    pass

_numpy_available = False
try:
    import numpy as np
    _numpy_available = True
except ImportError:
    pass

_opuslib_available = False
try:
    import opuslib
    _opuslib_available = True
except ImportError:
    pass


# ===========================================================================
# HTTP helpers
# ===========================================================================
def _make_auth_header(username: str, password: str) -> str:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"


def http_get(url: str, timeout: int = DEVICE_TIMEOUT,
             username: str = "", password: str = "") -> Tuple[Optional[int], Optional[bytes]]:
    req = urllib.request.Request(url)
    if username and password:
        req.add_header("Authorization", _make_auth_header(username, password))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return None, str(exc).encode()


def http_post(url: str, body: Any, timeout: int = DEVICE_TIMEOUT,
              username: str = "", password: str = "") -> Tuple[Optional[int], Optional[bytes]]:
    if isinstance(body, dict):
        data = json.dumps(body).encode()
    elif isinstance(body, str):
        data = body.encode()
    else:
        data = body
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if username and password:
        req.add_header("Authorization", _make_auth_header(username, password))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return None, str(exc).encode()


def get_json(url: str, timeout: int = DEVICE_TIMEOUT,
             username: str = "", password: str = "") -> Optional[Dict]:
    code, body = http_get(url, timeout=timeout, username=username, password=password)
    if code == 200 and body:
        try:
            return json.loads(body)
        except Exception:
            return None
    return None


def device_status(ip: str) -> Optional[Dict]:
    return get_json(f"http://{ip}/api/status", timeout=DEVICE_TIMEOUT,
                    username=DEVICE_USER, password=DEVICE_PASS)


def hub_url(path: str) -> str:
    return f"http://{HUB_IP}:{HUB_PORT}{path}"


def get_audio_stats() -> Optional[Dict]:
    return get_json(hub_url("/api/audio_stats"), timeout=HUB_TIMEOUT)


def reset_audio_stats() -> bool:
    code, _ = http_post(hub_url("/api/audio_stats"), {}, timeout=HUB_TIMEOUT)
    return code == 200


def get_hub_state() -> Optional[str]:
    stats = get_audio_stats()
    if stats:
        return stats.get("current_state")
    return None


def ensure_hub_idle(timeout: float = 15.0, label: str = "") -> Tuple[bool, str]:
    prefix = f"[{label}] " if label else ""
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        state = get_hub_state()
        if state is None:
            return False, f"{prefix}Hub unreachable"
        if state == "idle":
            return True, f"{prefix}Hub idle"
        last_state = state
        time.sleep(1.0)
    return False, f"{prefix}Hub stuck in '{last_state}' after {timeout:.0f}s"


def hub_packet_count(stats: Optional[Dict], device_id: str) -> int:
    if not stats:
        return 0
    senders = stats.get("senders", {})
    entry = senders.get(device_id, {})
    return entry.get("packet_count", 0)


def check_sequence_continuity(stats: Optional[Dict], device_id: str) -> Dict:
    """Check sequence continuity for a sender in hub audio_stats.

    Uses seq_min, seq_max, packet_count already returned by /api/audio_stats.
    Returns a dict with analysis results.
    """
    if not stats:
        return {"ok": False, "error": "no stats"}
    senders = stats.get("senders", {})
    sender = senders.get(device_id)
    if not sender:
        return {"ok": False, "error": f"sender {device_id} not found"}
    seq_min = sender.get("seq_min", 0)
    seq_max = sender.get("seq_max", 0)
    pkt_count = sender.get("packet_count", 0)
    expected = seq_max - seq_min + 1
    if expected <= 0:
        return {"ok": True, "expected": 0, "received": pkt_count, "lost": 0, "loss_pct": 0.0}
    lost = expected - pkt_count
    loss_pct = round(lost / expected * 100, 1) if expected > 0 else 0.0
    return {
        "ok": lost <= 0,
        "expected": expected,
        "received": pkt_count,
        "lost": max(0, lost),
        "loss_pct": max(0.0, loss_pct),
        "seq_min": seq_min,
        "seq_max": seq_max,
    }


def start_audio_capture() -> bool:
    """Enable hub audio capture buffer."""
    code, _ = http_post(hub_url("/api/audio_capture"), {"action": "start"}, timeout=HUB_TIMEOUT)
    return code == 200


def stop_audio_capture() -> bool:
    """Disable hub audio capture buffer."""
    code, _ = http_post(hub_url("/api/audio_capture"), {"action": "stop"}, timeout=HUB_TIMEOUT)
    return code == 200


def fetch_audio_capture(direction: Optional[str] = None,
                        device_id: Optional[str] = None,
                        since: Optional[float] = None,
                        limit: int = 500) -> Optional[Dict]:
    """Fetch captured audio frames from hub."""
    params = []
    if direction:
        params.append(f"direction={direction}")
    if device_id:
        params.append(f"device_id={device_id}")
    if since is not None:
        params.append(f"since={since}")
    if limit != 500:
        params.append(f"limit={limit}")
    qs = "&".join(params)
    url = hub_url(f"/api/audio_capture{'?' + qs if qs else ''}")
    return get_json(url, timeout=HUB_TIMEOUT)


class AudioAnalyzer:
    """Decode and analyze Opus audio frames captured by the hub.

    Requires: opuslib, numpy
    """

    @staticmethod
    def decode_frames(opus_b64_list, sample_rate=16000):
        """Decode list of base64-encoded Opus frames to numpy PCM array.

        Returns numpy int16 array, or None if dependencies unavailable.
        """
        if not _opuslib_available or not _numpy_available:
            return None
        if not opus_b64_list:
            return None

        decoder = opuslib.Decoder(sample_rate, 1)
        pcm_chunks = []
        for b64 in opus_b64_list:
            try:
                opus_data = base64.b64decode(b64)
                pcm = decoder.decode(opus_data, 320)  # 20ms at 16kHz = 320 samples
                pcm_chunks.append(np.frombuffer(pcm, dtype=np.int16))
            except Exception:
                # Insert silence for corrupt frames
                pcm_chunks.append(np.zeros(320, dtype=np.int16))
        if not pcm_chunks:
            return None
        return np.concatenate(pcm_chunks)

    @staticmethod
    def dominant_frequency(pcm, sample_rate=16000):
        """Find dominant frequency via FFT. Returns Hz or 0.0 on failure."""
        if not _numpy_available or pcm is None or len(pcm) < 320:
            return 0.0
        # Use float for FFT
        signal = pcm.astype(np.float64)
        # Window to reduce spectral leakage
        window = np.hanning(len(signal))
        windowed = signal * window
        fft = np.fft.rfft(windowed)
        magnitudes = np.abs(fft)
        # Ignore DC component
        magnitudes[0] = 0
        if len(magnitudes) < 2:
            return 0.0
        peak_idx = np.argmax(magnitudes)
        freq = peak_idx * sample_rate / len(signal)
        return float(freq)

    @staticmethod
    def snr(pcm, expected_freq, sample_rate=16000):
        """Estimate SNR in dB relative to expected frequency."""
        if not _numpy_available or pcm is None or len(pcm) < 320:
            return 0.0
        signal = pcm.astype(np.float64)
        window = np.hanning(len(signal))
        windowed = signal * window
        fft = np.fft.rfft(windowed)
        magnitudes = np.abs(fft)
        freq_resolution = sample_rate / len(signal)

        # Find signal bin (expected_freq +/- 2 bins)
        signal_bin = int(round(expected_freq / freq_resolution))
        signal_power = 0.0
        noise_power = 0.0
        for i in range(1, len(magnitudes)):  # skip DC
            power = magnitudes[i] ** 2
            if abs(i - signal_bin) <= 2:
                signal_power += power
            else:
                noise_power += power

        if noise_power < 1e-10:
            return 60.0  # Essentially perfect
        return 10 * np.log10(signal_power / noise_power) if signal_power > 0 else 0.0

    @staticmethod
    def check_audio_quality(opus_b64_list, expected_freq=440.0, sample_rate=16000):
        """Full audio quality report from captured Opus frames.

        Returns dict with:
            dominant_freq, freq_match (within 5%), snr_db, snr_ok (>15dB),
            clipping (any sample at +/-32767), duration_s, frame_count,
            non_silent (RMS > 100)
        """
        pcm = AudioAnalyzer.decode_frames(opus_b64_list, sample_rate)
        if pcm is None:
            return {"error": "decode failed or missing dependencies"}

        dom_freq = AudioAnalyzer.dominant_frequency(pcm, sample_rate)
        snr_db = AudioAnalyzer.snr(pcm, expected_freq, sample_rate) if expected_freq > 0 else 0.0

        rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
        clipping = bool(np.any(np.abs(pcm) >= 32767))
        duration = len(pcm) / sample_rate

        freq_tolerance = 0.05  # 5%
        freq_match = (abs(dom_freq - expected_freq) / expected_freq < freq_tolerance
                      if expected_freq > 0 else True)

        return {
            "dominant_freq": round(dom_freq, 1),
            "freq_match": freq_match,
            "snr_db": round(snr_db, 1),
            "snr_ok": snr_db > 15.0,
            "clipping": clipping,
            "duration_s": round(duration, 2),
            "frame_count": len(opus_b64_list),
            "non_silent": rms > 100,
            "rms": round(rms, 1),
        }


def post_test_action(ip: str, action: str, extra: Optional[Dict] = None,
                     timeout: int = DEVICE_TIMEOUT) -> Tuple[Optional[int], Optional[Dict]]:
    body = {"action": action}
    if extra:
        body.update(extra)
    code, raw = http_post(f"http://{ip}/api/test", body, timeout=timeout,
                          username=DEVICE_USER, password=DEVICE_PASS)
    if code is None:
        return None, None
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw.decode(errors="replace")}
    return code, parsed


def trigger_sustained_tx(ip: str, duration: int = 5) -> bool:
    code, _ = post_test_action(ip, "sustained_tx", {"duration": duration})
    return code == 200


def wait_for_tx_complete(ip: str, timeout: float = None, duration: int = 5) -> bool:
    if timeout is None:
        timeout = duration + 10
    deadline = time.time() + timeout
    time.sleep(max(0, duration - 1))
    while time.time() < deadline:
        st = device_status(ip)
        if st and not st.get("transmitting") and not st.get("sustained_tx_active"):
            return True
        time.sleep(0.5)
    return False


def reboot_device(ip: str, wait: bool = True, timeout: float = 30) -> bool:
    """Reboot a device via /api/test action=reboot. Waits for it to come back."""
    code, _ = post_test_action(ip, "reboot")
    if code not in (200, 0):  # 0 = connection reset (expected during reboot)
        return False
    if not wait:
        return True
    time.sleep(5)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = device_status(ip)
        if st and st.get("mqtt_connected"):
            return True
        time.sleep(2)
    return False


# ===========================================================================
# MQTT helpers
# ===========================================================================
def mqtt_publish(topic: str, payload: str, qos: int = 0) -> bool:
    if not _paho_available:
        return False
    try:
        c = mqtt.Client(client_id="qa_harness_pub", clean_session=True)
        c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.loop_start()
        res = c.publish(topic, payload, qos=qos)
        res.wait_for_publish(timeout=5)
        c.loop_stop()
        c.disconnect()
        return True
    except Exception:
        return False


class MqttSession:
    def __init__(self, client_id: str = "qa_session_pub"):
        self._client_id = client_id
        self._client = None
        self._connected = False

    def __enter__(self):
        if not _paho_available:
            return self
        try:
            self._client = mqtt.Client(client_id=self._client_id, clean_session=True)
            self._client.username_pw_set(MQTT_USER, MQTT_PASS)
            self._client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            self._client.loop_start()
            self._connected = True
        except Exception:
            self._client = None
            self._connected = False
        return self

    def __exit__(self, *_):
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False

    def publish(self, topic: str, payload: str, qos: int = 0) -> bool:
        if not self._connected or self._client is None:
            return False
        try:
            self._client.publish(topic, payload, qos=qos)
            return True
        except Exception:
            return False


# ===========================================================================
# SerialLogMonitor
# ===========================================================================
class SerialLogMonitor:
    """
    Reads ESP32 serial output from USB-UART ports in background threads.
    Stores timestamped lines for pattern matching during tests.
    """

    def __init__(self):
        self._ports = {}
        self._buffers = {}
        self._locks = {}
        self._threads = {}
        self._stop_event = threading.Event()

    def start(self):
        if not _serial_available:
            print("  NOTE: pyserial not installed -- serial monitoring disabled")
            return
        for name, port_path in SERIAL_PORTS.items():
            self._locks[name] = threading.Lock()
            self._buffers[name] = []
            try:
                ser = serial.Serial(port_path, baudrate=115200, timeout=0.5)
                self._ports[name] = ser
                t = threading.Thread(target=self._reader_loop, args=(name, ser),
                                     daemon=True, name=f"serial-{name}")
                self._threads[name] = t
                t.start()
                print(f"    Serial monitor: {name} ({port_path}) -- active")
            except Exception as exc:
                print(f"    Serial monitor: {name} ({port_path}) -- FAILED: {exc}")

    def stop(self):
        self._stop_event.set()
        for t in self._threads.values():
            t.join(timeout=2.0)
        for ser in self._ports.values():
            try:
                ser.close()
            except Exception:
                pass
        self._ports.clear()
        self._threads.clear()

    def is_active(self, device: str) -> bool:
        return device in self._ports and self._ports[device].is_open

    def mark(self, device: str = None) -> dict:
        result = {"time": time.time(), "indices": {}}
        devices = [device] if device else list(self._locks.keys())
        for name in devices:
            if name in self._locks:
                with self._locks[name]:
                    result["indices"][name] = len(self._buffers[name])
        return result

    def get_lines_since(self, device: str, since) -> List[str]:
        if device not in self._locks:
            return []
        with self._locks[device]:
            buf = self._buffers[device]
            if isinstance(since, dict):
                idx = since.get("indices", {}).get(device, 0)
                return [line for _, line in buf[idx:]]
            elif isinstance(since, (int, float)) and since > 1e9:
                return [line for ts, line in buf if ts >= since]
            else:
                return [line for _, line in buf[int(since):]]

    def get_timestamped_lines_since(self, device: str, since) -> List[Tuple[float, str]]:
        if device not in self._locks:
            return []
        with self._locks[device]:
            buf = self._buffers[device]
            if isinstance(since, dict):
                idx = since.get("indices", {}).get(device, 0)
                return list(buf[idx:])
            elif isinstance(since, (int, float)) and since > 1e9:
                return [(ts, line) for ts, line in buf if ts >= since]
            else:
                return list(buf[int(since):])

    def wait_for_pattern(self, device: str, pattern: str,
                         timeout: float = 5.0, since=None) -> Optional[str]:
        if device not in self._locks:
            return None
        compiled = re.compile(pattern, re.IGNORECASE)
        deadline = time.time() + timeout
        if since is not None:
            if isinstance(since, dict):
                start_idx = since.get("indices", {}).get(device, 0)
            elif isinstance(since, (int, float)):
                start_idx = int(since)
            else:
                start_idx = 0
        else:
            with self._locks[device]:
                start_idx = len(self._buffers[device])
        while time.time() < deadline:
            with self._locks[device]:
                buf = self._buffers[device]
                for i in range(start_idx, len(buf)):
                    _, line = buf[i]
                    if compiled.search(line):
                        return line
                start_idx = len(buf)
            time.sleep(0.1)
        return None

    def line_count(self, device: str) -> int:
        if device not in self._locks:
            return 0
        with self._locks[device]:
            return len(self._buffers[device])

    def _reader_loop(self, name: str, ser):
        while not self._stop_event.is_set():
            try:
                raw = ser.readline()
                if raw:
                    try:
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    except Exception:
                        line = repr(raw)
                    if line:
                        now = time.time()
                        with self._locks[name]:
                            self._buffers[name].append((now, line))
                            if len(self._buffers[name]) > 50000:
                                self._buffers[name] = self._buffers[name][-40000:]
            except Exception:
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)


# ===========================================================================
# HubLogMonitor
# ===========================================================================
class HubLogMonitor:
    """
    Captures hub add-on stdout via SSH in a background thread.
    Uses: ssh root@10.0.0.8 "ha apps logs local_intercom_hub --follow"
    """

    def __init__(self):
        self._buffer = []
        self._lock = threading.Lock()
        self._proc = None
        self._thread = None
        self._stop_event = threading.Event()
        self._active = False

    def start(self):
        try:
            self._proc = subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                 f"root@{HUB_IP}",
                 "ha apps logs local_intercom_hub --follow"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
            )
            self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                            name="hub-log-monitor")
            self._thread.start()
            self._active = True
            print(f"    Hub log monitor: SSH to {HUB_IP} -- active")
        except Exception as exc:
            print(f"    Hub log monitor: SSH to {HUB_IP} -- FAILED: {exc}")
            self._active = False

    def stop(self):
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=3)
        self._active = False

    def is_active(self) -> bool:
        return self._active and self._proc is not None and self._proc.poll() is None

    def mark(self) -> dict:
        with self._lock:
            return {"time": time.time(), "index": len(self._buffer)}

    def get_lines_since(self, since) -> List[str]:
        with self._lock:
            if isinstance(since, dict):
                idx = since.get("index", 0)
                return [line for _, line in self._buffer[idx:]]
            elif isinstance(since, (int, float)) and since > 1e9:
                return [line for ts, line in self._buffer if ts >= since]
            else:
                return [line for _, line in self._buffer[int(since):]]

    def get_timestamped_lines_since(self, since) -> List[Tuple[float, str]]:
        with self._lock:
            if isinstance(since, dict):
                idx = since.get("index", 0)
                return list(self._buffer[idx:])
            elif isinstance(since, (int, float)) and since > 1e9:
                return [(ts, line) for ts, line in self._buffer if ts >= since]
            else:
                return list(self._buffer[int(since):])

    def line_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _reader_loop(self):
        while not self._stop_event.is_set():
            if self._proc is None or self._proc.stdout is None:
                break
            try:
                raw = self._proc.stdout.readline()
                if not raw:
                    if self._proc.poll() is not None:
                        self._active = False
                        break
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    now = time.time()
                    with self._lock:
                        self._buffer.append((now, line))
                        if len(self._buffer) > 50000:
                            self._buffer = self._buffer[-40000:]
            except Exception:
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)


# ===========================================================================
# MQTTMonitor
# ===========================================================================
class MQTTMonitor:
    """
    Subscribes to intercom/# and captures all MQTT messages in a rolling buffer.
    """

    def __init__(self):
        self._buffer = []
        self._lock = threading.Lock()
        self._client = None
        self._active = False

    def start(self):
        if not _paho_available:
            print("    MQTT monitor: paho-mqtt not installed -- disabled")
            return
        try:
            self._client = mqtt.Client(client_id="qa_mqtt_monitor", clean_session=True)
            self._client.username_pw_set(MQTT_USER, MQTT_PASS)
            self._client.on_message = self._on_message
            self._client.on_connect = self._on_connect
            self._client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self._client.loop_start()
            self._active = True
            print(f"    MQTT monitor: subscribed to intercom/# on {MQTT_HOST} -- active")
        except Exception as exc:
            print(f"    MQTT monitor: FAILED: {exc}")
            self._active = False

    def stop(self):
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._active = False

    def is_active(self) -> bool:
        return self._active

    def mark(self) -> dict:
        with self._lock:
            return {"time": time.time(), "index": len(self._buffer)}

    def get_messages_since(self, since) -> List[Dict]:
        with self._lock:
            if isinstance(since, dict):
                idx = since.get("index", 0)
                return list(self._buffer[idx:])
            elif isinstance(since, (int, float)) and since > 1e9:
                return [m for m in self._buffer if m["time"] >= since]
            else:
                return list(self._buffer[int(since):])

    def message_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe("intercom/#", qos=0)

    def _on_message(self, client, userdata, msg):
        now = time.time()
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = repr(msg.payload)
        with self._lock:
            self._buffer.append({
                "time": now,
                "topic": msg.topic,
                "payload": payload,
            })
            if len(self._buffer) > 50000:
                self._buffer = self._buffer[-40000:]


# ===========================================================================
# CrashDetector
# ===========================================================================
class CrashDetector:
    """
    Detects device crashes/reboots by:
    1. Comparing uptime_seconds before and after each test
    2. Scanning serial logs for crash patterns
    """

    def __init__(self, serial_monitor: Optional[SerialLogMonitor] = None):
        self._serial = serial_monitor
        self._initial_reset_reasons = {}

    def record_initial_state(self):
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st:
                self._initial_reset_reasons[name] = st.get("reset_reason", "unknown")

    def snapshot_uptimes(self) -> Dict[str, Optional[int]]:
        result = {}
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st:
                result[name] = st.get("uptime_seconds")
            else:
                result[name] = None
        return result

    def check_for_reboot(self, before: Dict[str, Optional[int]],
                         after: Dict[str, Optional[int]]) -> List[str]:
        issues = []
        for name in DEVICES:
            b = before.get(name)
            a = after.get(name)
            if b is not None and a is not None:
                if a < b:
                    issues.append(f"{name} REBOOTED (uptime {b}s -> {a}s)")
            elif b is not None and a is None:
                issues.append(f"{name} UNREACHABLE after test (was up {b}s)")
        return issues

    def scan_serial_crashes(self, serial_marker: Optional[dict]) -> List[str]:
        if not self._serial or serial_marker is None:
            return []
        crashes = []
        for name in DEVICES:
            lines = self._serial.get_lines_since(name, serial_marker)
            for line in lines:
                if CRASH_RE.search(line):
                    crashes.append(f"{name}: {line.strip()}")
        return crashes

    def check_reset_reason_changed(self) -> List[str]:
        issues = []
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st:
                current = st.get("reset_reason", "unknown")
                initial = self._initial_reset_reasons.get(name, "unknown")
                if current != initial and current in ("Crash/Panic", "Interrupt watchdog",
                                                       "Task watchdog timeout", "Watchdog"):
                    issues.append(f"{name} reset_reason changed: {initial} -> {current}")
        return issues


# ===========================================================================
# WebSocket PTT Simulation
# ===========================================================================
class WebPTTClient:
    """
    Simulates a Web PTT client via WebSocket.
    Connects to hub, sends ptt_start, streams PCM frames, sends ptt_stop.
    """

    def __init__(self, client_name: str = "QA_WebPTT"):
        self._client_name = client_name
        self._ws = None
        self._loop = None
        self._thread = None

    async def _connect(self):
        if not _websockets_available:
            raise RuntimeError("websockets package not installed")
        self._ws = await websockets.connect(f"ws://{HUB_IP}:{HUB_PORT}/ws")
        await self._ws.send(json.dumps({
            "type": "identify",
            "client_id": self._client_name,
        }))
        # Read init message
        try:
            await asyncio.wait_for(self._ws.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def _ptt_start(self, target: str = "All Rooms", priority: str = "Normal"):
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "ptt_start",
                "target": target,
                "priority": priority,
            }))
            # Read state response
            try:
                await asyncio.wait_for(self._ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _send_audio(self, duration: float = 3.0, frequency: float = 440.0):
        """Send PCM frames at 50fps (20ms frames, 640 bytes each)."""
        if not self._ws:
            return
        frames = int(duration * 50)
        samples_per_frame = 320
        for i in range(frames):
            frame_start = time.monotonic()
            pcm = bytearray(640)
            for s in range(samples_per_frame):
                t = (i * samples_per_frame + s) / 16000.0
                val = int(16000 * math.sin(2 * math.pi * frequency * t))
                val = max(-32768, min(32767, val))
                struct.pack_into("<h", pcm, s * 2, val)
            await self._ws.send(bytes(pcm))
            elapsed = time.monotonic() - frame_start
            sleep_time = 0.02 - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _ptt_stop(self):
        if self._ws:
            await self._ws.send(json.dumps({"type": "ptt_stop"}))
            try:
                await asyncio.wait_for(self._ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _disconnect(self):
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _run_session(self, target: str, duration: float, priority: str,
                           frequency: float, disconnect_without_stop: bool):
        await self._connect()
        await self._ptt_start(target=target, priority=priority)
        await self._send_audio(duration=duration, frequency=frequency)
        if not disconnect_without_stop:
            await self._ptt_stop()
        await self._disconnect()

    def transmit(self, target: str = "All Rooms", duration: float = 3.0,
                 priority: str = "Normal", frequency: float = 440.0,
                 disconnect_without_stop: bool = False) -> bool:
        """
        Run a complete PTT session synchronously. Returns True on success.
        """
        if not _websockets_available:
            return False
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._run_session(
                target=target, duration=duration, priority=priority,
                frequency=frequency, disconnect_without_stop=disconnect_without_stop))
            loop.close()
            return True
        except Exception as exc:
            print(f"      WebPTT error: {exc}")
            return False

    def transmit_async(self, target: str = "All Rooms", duration: float = 3.0,
                       priority: str = "Normal", frequency: float = 440.0,
                       disconnect_without_stop: bool = False) -> threading.Thread:
        """Start PTT session in a background thread. Returns the thread."""
        t = threading.Thread(
            target=self.transmit,
            kwargs=dict(target=target, duration=duration, priority=priority,
                        frequency=frequency, disconnect_without_stop=disconnect_without_stop),
            daemon=True, name=f"web-ptt-{self._client_name}")
        t.start()
        return t


# ===========================================================================
# LogHarness — orchestrates all monitors
# ===========================================================================
class TestResult:
    """Result of a single test, including captured logs."""

    def __init__(self, test_id: str, name: str):
        self.test_id = test_id
        self.name = name
        self.status = "PENDING"  # PASS, FAIL, SKIP
        self.detail = ""
        self.start_time = None
        self.end_time = None
        self.uptimes_before = {}
        self.uptimes_after = {}
        self.reboot_issues = []
        self.crash_patterns = []
        self.stale_audio = False
        self.stale_detail = ""
        self.logs = {
            "serial_bedroom": [],
            "serial_intercom2": [],
            "hub": [],
            "mqtt": [],
        }

    def duration_s(self) -> float:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0.0


class LogHarness:
    """
    Captures serial, hub, and MQTT logs with per-test segmentation,
    crash detection, and stale audio verification.
    """

    def __init__(self):
        self.serial = SerialLogMonitor()
        self.hub = HubLogMonitor()
        self.mqtt = MQTTMonitor()
        self.crash_detector = CrashDetector(self.serial)
        self._test_markers = {}  # test_id -> {serial: marker, hub: marker, mqtt: marker}

    def start(self):
        print("\n  Starting log monitors:")
        self.serial.start()
        self.hub.start()
        self.mqtt.start()
        self.crash_detector.record_initial_state()
        print()

    def stop(self):
        self.serial.stop()
        self.hub.stop()
        self.mqtt.stop()

    def status_line(self) -> str:
        parts = []
        active = sum(1 for d in DEVICES if self.serial.is_active(d))
        parts.append(f"serial={active}/{len(DEVICES)}")
        parts.append(f"hub={'active' if self.hub.is_active() else 'off'}")
        parts.append(f"mqtt={'active' if self.mqtt.is_active() else 'off'}")
        return ", ".join(parts)

    def begin_test(self, test_id: str):
        self._test_markers[test_id] = {
            "serial": self.serial.mark(),
            "hub": self.hub.mark(),
            "mqtt": self.mqtt.mark(),
            "time": time.time(),
        }

    def end_test(self, test_id: str, result: TestResult):
        markers = self._test_markers.get(test_id, {})
        serial_marker = markers.get("serial", {})

        # Collect logs from all sources
        for device in ["bedroom", "intercom2"]:
            result.logs[f"serial_{device}"] = self.serial.get_lines_since(device, serial_marker)

        result.logs["hub"] = self.hub.get_lines_since(markers.get("hub", {}))
        result.logs["mqtt"] = self.mqtt.get_messages_since(markers.get("mqtt", {}))

        # Check for crashes in serial logs
        result.crash_patterns = self.crash_detector.scan_serial_crashes(serial_marker)

        # Check for reboots
        result.reboot_issues = self.crash_detector.check_for_reboot(
            result.uptimes_before, result.uptimes_after)

    def check_stale_audio(self) -> Tuple[bool, str]:
        """
        Verify no stale audio is flowing after a test.
        Returns (is_clean, detail).
        """
        # Verify all devices stopped transmitting
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st and (st.get("transmitting") or st.get("sustained_tx_active")):
                return False, f"{name} still transmitting"

        # Verify hub idle
        state = get_hub_state()
        if state and state != "idle":
            return False, f"Hub state={state}, expected idle"

        # Snapshot rx counts
        rx_before = {}
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st:
                rx_before[name] = st.get("rx_packet_count", 0)

        # Wait 2 seconds
        time.sleep(2.0)

        # Re-check rx counts
        issues = []
        for name, info in DEVICES.items():
            st = device_status(info["ip"])
            if st:
                rx_after = st.get("rx_packet_count", 0)
                delta = rx_after - rx_before.get(name, 0)
                if delta > 0:
                    issues.append(f"{name} rx increased by {delta} during silence window")
                if st.get("receiving"):
                    issues.append(f"{name} still receiving")

        if issues:
            return False, "; ".join(issues)
        return True, "clean"


# ===========================================================================
# Test Runner
# ===========================================================================
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# Trailing log wait time (seconds)
TRAILING_LOG_WAIT = 3  # Keep short for speed; logs are already captured continuously


def run_test(test_id: str, name: str, fn: Callable, harness: LogHarness,
             check_stale: bool = True) -> TestResult:
    """
    Execute a single test with full instrumentation:
    1. Snapshot uptimes
    2. Mark all log streams
    3. Execute test function
    4. Wait for trailing logs
    5. Collect logs
    6. Check for crashes/reboots
    7. Check for stale audio
    8. Return instrumented result
    """
    result = TestResult(test_id, name)
    result.start_time = time.time()

    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"\n  [{now_str}] Running {test_id}: {name}", flush=True)

    # Pre-test: snapshot uptimes
    result.uptimes_before = harness.crash_detector.snapshot_uptimes()
    pre_str = " | ".join(
        f"{n}={v}s" for n, v in result.uptimes_before.items() if v is not None)
    print(f"    PRE: {pre_str}", flush=True)

    # Mark all log streams
    harness.begin_test(test_id)

    # Execute test
    try:
        status, detail = fn()
        result.status = status
        result.detail = detail
    except Exception as exc:
        result.status = FAIL
        result.detail = f"EXCEPTION: {exc}"

    result.end_time = time.time()

    # Wait for trailing logs
    time.sleep(TRAILING_LOG_WAIT)

    # Post-test: snapshot uptimes
    result.uptimes_after = harness.crash_detector.snapshot_uptimes()

    # Collect all logs and check crashes
    harness.end_test(test_id, result)

    # Override result if crash/reboot detected
    if result.reboot_issues:
        result.status = FAIL
        result.detail = f"REBOOT: {'; '.join(result.reboot_issues)} | {result.detail}"

    if result.crash_patterns:
        result.status = FAIL
        crash_summary = "; ".join(result.crash_patterns[:3])
        result.detail = f"CRASH: {crash_summary} | {result.detail}"

    # Check for stale audio (unless test is expected to leave state)
    if check_stale and result.status != SKIP:
        clean, stale_detail = harness.check_stale_audio()
        if not clean:
            result.stale_audio = True
            result.stale_detail = stale_detail

    # Print result
    now_str = datetime.now().strftime("%H:%M:%S")
    post_parts = []
    for n, v in result.uptimes_after.items():
        before_v = result.uptimes_before.get(n)
        if v is not None and before_v is not None:
            delta = v - before_v
            post_parts.append(f"{n}={v}s (+{delta}s)")
        elif v is None:
            post_parts.append(f"{n}=UNREACHABLE")
    post_str = " | ".join(post_parts)

    status_tag = result.status
    if result.stale_audio:
        status_tag += "+STALE"

    print(f"  {now_str}  [{status_tag}]  {test_id}: {name}", flush=True)
    print(f"    POST: {post_str}", flush=True)
    print(f"    DETAIL: {result.detail}", flush=True)

    # Log summary
    log_counts = (
        f"Bedroom serial: {len(result.logs['serial_bedroom'])} lines | "
        f"INTERCOM2 serial: {len(result.logs['serial_intercom2'])} lines | "
        f"Hub: {len(result.logs['hub'])} lines | "
        f"MQTT: {len(result.logs['mqtt'])} msgs"
    )
    print(f"    LOG: {log_counts}", flush=True)

    if result.stale_audio:
        print(f"    STALE AUDIO: {result.stale_detail}", flush=True)

    # On FAIL — dump all logs
    if result.status == FAIL:
        _dump_test_logs(result)

    return result


def _dump_test_logs(result: TestResult):
    """Print all captured logs for a failed test."""
    print(f"\n    --- SERIAL: Bedroom ({len(result.logs['serial_bedroom'])} lines) ---")
    for line in result.logs["serial_bedroom"][-50:]:
        print(f"      {line}")

    print(f"    --- SERIAL: INTERCOM2 ({len(result.logs['serial_intercom2'])} lines) ---")
    for line in result.logs["serial_intercom2"][-50:]:
        print(f"      {line}")

    print(f"    --- HUB LOG ({len(result.logs['hub'])} lines) ---")
    for line in result.logs["hub"][-30:]:
        print(f"      {line}")

    print(f"    --- MQTT ({len(result.logs['mqtt'])} messages) ---")
    for msg in result.logs["mqtt"][-20:]:
        t = msg.get("topic", "?")
        p = msg.get("payload", "?")
        if len(p) > 200:
            p = p[:200] + "..."
        print(f"      {t}: {p}")
    print()


# ===========================================================================
# Report Generation
# ===========================================================================
def generate_report(results: List[TestResult], harness: LogHarness,
                    output_path: str = "tests/qa_report.md"):
    """Write a markdown summary report."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Gather device info
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    bed_fw = bed_st.get("firmware_version", "?") if bed_st else "?"
    ic2_fw = ic2_st.get("firmware_version", "?") if ic2_st else "?"

    passed = sum(1 for r in results if r.status == PASS)
    failed = sum(1 for r in results if r.status == FAIL)
    skipped = sum(1 for r in results if r.status == SKIP)
    stale = sum(1 for r in results if r.stale_audio)
    reboots = sum(1 for r in results if r.reboot_issues)
    crashes = sum(1 for r in results if r.crash_patterns)
    total = len(results)

    lines = [
        f"# QA Test Report -- {now_str}",
        f"",
        f"Devices: Bedroom v{bed_fw} ({BEDROOM_IP}), INTERCOM2 v{ic2_fw} ({INTERCOM2_IP}), Hub ({HUB_IP}:{HUB_PORT})",
        f"Log capture: {harness.status_line()}",
        f"",
        f"## Summary",
        f"",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| PASS   | {passed} |",
        f"| FAIL   | {failed} |",
        f"| SKIP   | {skipped} |",
        f"| Stale audio warnings | {stale} |",
        f"| Reboots detected | {reboots} |",
        f"| Crash patterns found | {crashes} |",
        f"| **Total** | **{total}** |",
        f"",
        f"## Results",
        f"",
        f"| ID | Name | Status | Detail |",
        f"|----|------|--------|--------|",
    ]

    for r in results:
        status = r.status
        if r.stale_audio:
            status += "+STALE"
        detail = r.detail.replace("|", "\\|")
        if len(detail) > 120:
            detail = detail[:120] + "..."
        lines.append(f"| {r.test_id} | {r.name} | {status} | {detail} |")

    # Failures detail
    failures = [r for r in results if r.status == FAIL]
    if failures:
        lines.extend(["", "## Failures", ""])
        for r in failures:
            lines.append(f"### {r.test_id}: {r.name}")
            lines.append(f"**Detail:** {r.detail}")
            if r.reboot_issues:
                lines.append(f"**Reboots:** {', '.join(r.reboot_issues)}")
            if r.crash_patterns:
                lines.append(f"**Crash patterns:** {', '.join(r.crash_patterns[:5])}")
            lines.append("")

    # Write report
    report_text = "\n".join(lines) + "\n"
    with open(output_path, "w") as f:
        f.write(report_text)
    print(f"\n  Report written to {output_path}")
    return report_text


# ===========================================================================
# Console Summary
# ===========================================================================
def print_summary(results: List[TestResult]):
    """Print a console summary at the end of the run."""
    passed = sum(1 for r in results if r.status == PASS)
    failed = sum(1 for r in results if r.status == FAIL)
    skipped = sum(1 for r in results if r.status == SKIP)
    stale = sum(1 for r in results if r.stale_audio)
    total = len(results)

    print("\n" + "=" * 70)
    print("  TEST SUMMARY")
    print("=" * 70)
    print(f"  Total:   {total}")
    print(f"  PASS:    {passed}")
    print(f"  FAIL:    {failed}")
    print(f"  SKIP:    {skipped}")
    if stale:
        print(f"  STALE:   {stale} (audio leak warnings)")
    print()

    failures = [r for r in results if r.status == FAIL]
    if failures:
        print("  FAILURES:")
        for r in failures:
            print(f"    {r.test_id}: {r.name}")
            print(f"      {r.detail}")
        print()

    stale_tests = [r for r in results if r.stale_audio and r.status == PASS]
    if stale_tests:
        print("  STALE AUDIO WARNINGS (passed but leaked):")
        for r in stale_tests:
            print(f"    {r.test_id}: {r.stale_detail}")
        print()

    verdict = "ALL PASS" if failed == 0 else f"FAILURES DETECTED ({failed})"
    print("=" * 70)
    print(f"  RESULT: {verdict}")
    print("=" * 70)
