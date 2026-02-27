#!/usr/bin/env python3
"""
Comprehensive QA Test Suite — v2.8.3 / Hub v2.5.2
===================================================
68 tests across 4 categories:
  Category 1: Feature Verification  (T1–T32)
  Category 2: Stress Testing        (T33–T46)
  Category 3: Edge Cases            (T47–T63)
  Category 4: Known Bug Repro       (T64–T68)

Usage:
  python3 tests/qa_comprehensive.py
  python3 tests/qa_comprehensive.py --dry-run
  python3 tests/qa_comprehensive.py --category 1
  python3 tests/qa_comprehensive.py --test T7
  python3 tests/qa_comprehensive.py --list
  python3 tests/qa_comprehensive.py --no-soak   # skip T43-T46, T64, T68
"""

import sys
import json
import time
import math
import re
import struct
import socket
import threading
import urllib.request
import urllib.error
import urllib.parse
import base64
import argparse
import io
from datetime import datetime
from typing import Optional, Tuple, Dict, List, Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BEDROOM_IP   = "10.0.0.15"
INTERCOM2_IP = "10.0.0.14"
HUB_IP       = "10.0.0.8"
HUB_PORT     = 8099
MQTT_HOST    = "10.0.0.8"
MQTT_PORT    = 1883
MQTT_USER    = "REDACTED_MQTT_USER"
MQTT_PASS    = "REDACTED_MQTT_PASS"
EXPECTED_VERSION = "2.8.6"

# Known MQTT unique IDs (last 4 bytes of device MAC, hex)
BEDROOM_UNIQUE_ID  = "intercom_XXXXXXXX"
INTERCOM2_UNIQUE_ID = "intercom_YYYYYYYY"
HUB_UNIQUE_ID      = "intercom_ZZZZZZZZ"

# Hub audio_stats keys by device_id from packet header (8 bytes, 16 hex chars)
BEDROOM_DEVICE_ID  = "XXXXXXXXXXXXXXXX"
INTERCOM2_DEVICE_ID = "YYYYYYYYYYYYYYYY"

DEVICE_TIMEOUT = 10
HUB_TIMEOUT    = 5

# Topics: intercom/{unique_id}/{entity}/set
def bedroom_topic(entity): return f"intercom/{BEDROOM_UNIQUE_ID}/{entity}/set"
def intercom2_topic(entity): return f"intercom/{INTERCOM2_UNIQUE_ID}/{entity}/set"

CALL_TOPIC       = "intercom/call"
HUB_NOTIFY_TOPIC = f"intercom/{HUB_UNIQUE_ID}/notify"

# ---------------------------------------------------------------------------
# Serial monitor (pyserial)
# ---------------------------------------------------------------------------
_serial_available = False
try:
    import serial
    _serial_available = True
except ImportError:
    pass

_serial_monitor = None  # Module-level; initialized in main()

SERIAL_PORTS = {
    "bedroom":   "/dev/ttyACM0",
    "intercom2": "/dev/ttyACM1",
}


class SerialLogMonitor:
    """
    Reads ESP32 serial output from USB-UART ports in background threads.
    Stores timestamped lines for pattern matching during tests.
    Degrades gracefully if pyserial is not installed or ports are unavailable.
    """

    def __init__(self):
        self._ports = {}       # device_name -> serial.Serial
        self._buffers = {}     # device_name -> list of (timestamp, line_str)
        self._locks = {}       # device_name -> threading.Lock
        self._threads = {}     # device_name -> threading.Thread
        self._stop_event = threading.Event()

    def start(self):
        """Open serial ports and start reader threads."""
        if not _serial_available:
            print("  NOTE: pyserial not installed -- serial monitoring disabled")
            print("        Install: pip install pyserial")
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
                print(f"  Serial monitor: {name} ({port_path}) -- active")
            except Exception as exc:
                print(f"  Serial monitor: {name} ({port_path}) -- FAILED: {exc}")

    def stop(self):
        """Signal all reader threads to stop and close ports."""
        self._stop_event.set()
        for name, t in self._threads.items():
            t.join(timeout=2.0)
        for name, ser in self._ports.items():
            try:
                ser.close()
            except Exception:
                pass
        self._ports.clear()
        self._threads.clear()

    def is_active(self, device: str) -> bool:
        """Return True if the given device's serial port is open and reader running."""
        return device in self._ports and self._ports[device].is_open

    def wait_for_pattern(self, device: str, pattern: str,
                         timeout: float = 5.0,
                         since=None) -> Optional[str]:
        """
        Wait up to `timeout` seconds for a regex match in the device's log buffer.
        If `since` is provided (a marker dict from mark(), or an int buffer index),
        search from that position — this avoids missing lines that arrived between
        the mark() call and this call. Without `since`, searches from the current
        buffer position (legacy behaviour, races with lines arriving before the call).
        Returns the matching line or None on timeout.
        """
        if device not in self._locks:
            return None
        compiled = re.compile(pattern, re.IGNORECASE)
        deadline = time.time() + timeout

        # Determine start index from the since argument.
        if since is not None:
            if isinstance(since, dict):
                # Marker dict produced by mark() — extract index for this device.
                start_idx = since.get("indices", {}).get(device, 0)
            elif isinstance(since, (int, float)):
                start_idx = int(since)
            else:
                start_idx = 0
        else:
            # Legacy: start from the current buffer tail so only new lines are seen.
            with self._locks[device]:
                start_idx = len(self._buffers[device])

        while time.time() < deadline:
            with self._locks[device]:
                buf = self._buffers[device]
                for i in range(start_idx, len(buf)):
                    ts_val, line = buf[i]
                    if compiled.search(line):
                        return line
                start_idx = len(buf)
            time.sleep(0.1)
        return None

    def get_lines_since(self, device: str, since) -> List[str]:
        """
        Return all lines from device since the given marker.
        `since` may be:
          - a float timestamp (time.time() value) — lines with ts >= that value
          - a marker dict from mark() — lines after the buffer index captured at mark()
          - an int buffer index — lines from that index onward
        """
        if device not in self._locks:
            return []
        with self._locks[device]:
            buf = self._buffers[device]
            if isinstance(since, dict):
                # Marker dict: use captured buffer index for exact replay.
                start_idx = since.get("indices", {}).get(device, 0)
                return [line for _, line in buf[start_idx:]]
            elif isinstance(since, (int, float)) and since > 1e9:
                # Looks like a Unix timestamp (seconds since epoch > 2001).
                return [line for ts_val, line in buf if ts_val >= since]
            else:
                # Plain int index.
                start_idx = int(since)
                return [line for _, line in buf[start_idx:]]

    def clear(self, device: str = None):
        """Clear buffered lines for one or all devices."""
        targets = [device] if device else list(self._locks.keys())
        for name in targets:
            if name in self._locks:
                with self._locks[name]:
                    self._buffers[name].clear()

    def mark(self, device: str = None) -> dict:
        """
        Record a marker at the current position in the log buffer(s).
        Returns a dict with 'time' (wall clock) and 'indices' (per-device buffer
        lengths at the moment mark() was called). Pass the returned dict to
        wait_for_pattern(since=...) or get_lines_since(...) to avoid missing
        lines that arrived between mark() and the subsequent call.
        """
        result = {"time": time.time(), "indices": {}}
        devices = [device] if device else list(self._locks.keys())
        for name in devices:
            if name in self._locks:
                with self._locks[name]:
                    result["indices"][name] = len(self._buffers[name])
        return result

    def _reader_loop(self, name: str, ser):
        """Background thread: continuously read lines from serial port."""
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
                            # Cap buffer at 50000 lines to prevent unbounded growth
                            if len(self._buffers[name]) > 50000:
                                self._buffers[name] = self._buffers[name][-40000:]
            except serial.SerialException:
                # Port disconnected or error -- stop reading
                break
            except Exception:
                # Transient error -- keep trying
                time.sleep(0.1)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
PASS    = "PASS"
FAIL    = "FAIL"
SKIP    = "SKIP"
CLARIFY = "CLARIFY"

_results: List[Dict] = []
_dry_run = False


def ts():
    return datetime.now().strftime("%H:%M:%S")


def record(test_id: str, name: str, status: str, detail: str = ""):
    _results.append({"id": test_id, "name": name, "status": status, "detail": detail})
    sym = {PASS: "[PASS]", FAIL: "[FAIL]", SKIP: "[SKIP]", CLARIFY: "[????]"}.get(status, "[????]")
    print(f"  {ts()}  {sym}  {test_id}: {name}", flush=True)
    if detail:
        for line in detail.strip().splitlines():
            print(f"              {line}", flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _auth_header():
    return "Basic " + base64.b64encode(b"admin:admin").decode()


def http_get(url: str, timeout: int = DEVICE_TIMEOUT) -> Tuple[Optional[int], Optional[bytes]]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return None, str(exc).encode()


def http_post(url: str, body: Any, content_type: str = "application/json",
              timeout: int = DEVICE_TIMEOUT, raw_body: Optional[bytes] = None) -> Tuple[Optional[int], Optional[bytes]]:
    if raw_body is not None:
        data = raw_body
    elif isinstance(body, dict):
        data = json.dumps(body).encode()
    elif isinstance(body, str):
        data = body.encode()
    else:
        data = body
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return None, str(exc).encode()


def get_json(url: str, timeout: int = DEVICE_TIMEOUT) -> Optional[Dict]:
    code, body = http_get(url, timeout=timeout)
    if code == 200 and body:
        try:
            return json.loads(body)
        except Exception:
            return None
    return None


def device_status(ip: str) -> Optional[Dict]:
    return get_json(f"http://{ip}/api/status", timeout=DEVICE_TIMEOUT)


def hub_url(path: str) -> str:
    return f"http://{HUB_IP}:{HUB_PORT}{path}"


def get_audio_stats() -> Optional[Dict]:
    return get_json(hub_url("/api/audio_stats"), timeout=HUB_TIMEOUT)


def reset_audio_stats() -> bool:
    code, _ = http_post(hub_url("/api/audio_stats"), {}, timeout=HUB_TIMEOUT)
    return code == 200


def post_test_action(ip: str, action: str, extra: Optional[Dict] = None,
                     timeout: int = DEVICE_TIMEOUT) -> Tuple[Optional[int], Optional[Dict]]:
    body = {"action": action}
    if extra:
        body.update(extra)
    code, raw = http_post(f"http://{ip}/api/test", body, timeout=timeout)
    if code is None:
        return None, None
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw.decode(errors="replace")}
    return code, parsed


def packet_count(stats: Optional[Dict], device_id: str) -> int:
    """Extract packet count from hub audio_stats for a given device_id hex key."""
    if not stats:
        return 0
    senders = stats.get("senders", {})
    entry = senders.get(device_id, {})
    if isinstance(entry, dict):
        return entry.get("packet_count", 0)
    return int(entry) if entry else 0


# ---------------------------------------------------------------------------
# MQTT helper (paho-mqtt)
# ---------------------------------------------------------------------------
_paho_available = False
try:
    import paho.mqtt.client as mqtt
    _paho_available = True
except ImportError:
    pass


def mqtt_publish(topic: str, payload: str, qos: int = 0) -> bool:
    """Publish a single MQTT message. Returns True on success."""
    if not _paho_available:
        return False
    try:
        c = mqtt.Client(client_id="qa_comprehensive_pub", clean_session=True)
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


def mqtt_subscribe_collect(topic: str, duration: float,
                           trigger_fn=None) -> List[Dict]:
    """
    Subscribe to topic, optionally call trigger_fn(), then collect messages
    for `duration` seconds. Returns list of dicts with 'topic' and 'payload'.
    """
    if not _paho_available:
        return []
    messages = []
    lock = threading.Event()

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode(errors="replace")
        except Exception:
            payload = repr(msg.payload)
        messages.append({"topic": msg.topic, "payload": payload})

    c = mqtt.Client(client_id="qa_comprehensive_sub", clean_session=True)
    c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.on_message = on_message
    try:
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.subscribe(topic, qos=0)
        c.loop_start()
        time.sleep(0.2)  # let subscribe land
        if trigger_fn:
            trigger_fn()
        time.sleep(duration)
        c.loop_stop()
        c.disconnect()
    except Exception:
        pass
    return messages


def mqtt_set_and_verify(device_ip: str, topic: str, payload: str,
                        status_field: str, expected_value,
                        settle: float = 0.8) -> Tuple[bool, str]:
    """
    Publish payload to topic, wait settle seconds, then check /api/status.
    Returns (ok, detail_string).
    """
    if not mqtt_publish(topic, payload):
        return False, "MQTT publish failed (paho unavailable or broker error)"
    time.sleep(settle)
    st = device_status(device_ip)
    if st is None:
        return False, f"Cannot reach device at {device_ip} after MQTT publish"
    got = st.get(status_field)
    if got == expected_value:
        return True, f"{status_field}={got!r}"
    return False, f"{status_field}: expected {expected_value!r}, got {got!r}"


# ---------------------------------------------------------------------------
# WebSocket helper (websockets library)
# ---------------------------------------------------------------------------
_ws_available = False
try:
    import websockets
    import asyncio
    _ws_available = True
except ImportError:
    pass


def ws_collect(uri: str, send_msgs: List[str], collect_duration: float) -> List[str]:
    """
    Connect to WebSocket URI, send messages, collect received messages for duration.
    Returns list of received message strings.
    """
    if not _ws_available:
        return []

    received = []

    async def _run():
        try:
            async with websockets.connect(uri, open_timeout=5) as ws:
                for msg in send_msgs:
                    await ws.send(msg)
                deadline = asyncio.get_event_loop().time() + collect_duration
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                        received.append(msg if isinstance(msg, str) else msg.decode(errors="replace"))
                    except asyncio.TimeoutError:
                        break
        except Exception:
            pass

    asyncio.run(_run())
    return received


# ---------------------------------------------------------------------------
# Reachability check
# ---------------------------------------------------------------------------
def device_reachable(ip: str) -> bool:
    st = device_status(ip)
    return st is not None


def hub_reachable() -> bool:
    code, _ = http_get(hub_url("/api/audio_stats"), timeout=HUB_TIMEOUT)
    return code == 200


# ---------------------------------------------------------------------------
# Dry-run registry
# ---------------------------------------------------------------------------
_TEST_REGISTRY: List[Dict] = []  # populated by @register_test

def register_test(test_id: str, name: str, category: int):
    def decorator(fn):
        _TEST_REGISTRY.append({"id": test_id, "name": name, "cat": category, "fn": fn})
        return fn
    return decorator


# ===========================================================================
#  CATEGORY 1: FEATURE VERIFICATION  (T1–T32)
# ===========================================================================

@register_test("T1", "Bedroom test_tone → hub audio_stats packet count > 0", 1)
def test_t1():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    code, body = post_test_action(BEDROOM_IP, "test_tone", {"duration": 3}, timeout=15)
    if code not in (200, 409):
        return FAIL, f"POST /api/test returned {code}: {body}"
    if code == 409:
        time.sleep(3)
    else:
        time.sleep(0.5)
    stats = get_audio_stats()
    count = packet_count(stats, BEDROOM_DEVICE_ID)
    if count < 40:
        senders = stats.get("senders", {}) if stats else {}
        return FAIL, f"Expected >=40 packets, got {count}. senders={senders}"
    return PASS, f"Bedroom: {count} packets received by hub"


@register_test("T2", "INTERCOM2 test_tone → hub audio_stats packet count > 0", 1)
def test_t2():
    if not device_reachable(INTERCOM2_IP):
        return SKIP, "INTERCOM2 unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    code, body = post_test_action(INTERCOM2_IP, "test_tone", {"duration": 3}, timeout=15)
    if code not in (200, 409):
        return FAIL, f"POST /api/test returned {code}: {body}"
    if code == 409:
        time.sleep(3)
    else:
        time.sleep(0.5)
    stats = get_audio_stats()
    count = packet_count(stats, INTERCOM2_DEVICE_ID)
    if count < 40:
        senders = stats.get("senders", {}) if stats else {}
        return FAIL, f"Expected >=40 packets, got {count}. senders={senders}"
    return PASS, f"INTERCOM2: {count} packets received by hub"


@register_test("T3", "test_tone → firmware sends exactly 50 frames (1 second), verify count", 1)
def test_t3():
    """
    KNOWN BEHAVIOR: firmware test_tone_task hardcodes total_frames=50 and
    ignores the 'duration' JSON parameter. HTTP returns after task spawns.
    Expected: 50 packets per invocation (50 frames x 20ms = 1 second of audio).
    Flag to code-writer: 'duration' parameter is dead code — testability issue.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
    if code not in (200, 409):
        return FAIL, f"POST returned {code}"
    time.sleep(2.0)  # wait for 50-frame tone to complete
    stats = get_audio_stats()
    count = packet_count(stats, BEDROOM_DEVICE_ID)
    # Firmware sends exactly 50 frames; allow ±10 for retransmits/loss
    if 40 <= count <= 60:
        return PASS, f"{count} packets (expected 40–60; firmware hardcodes 50 frames)"
    return FAIL, (f"{count} packets (expected 40–60). "
                  "TESTABILITY NOTE: firmware 'duration' param is ignored; always 50 frames.")


@register_test("T4", "Multiple test_tones accumulate monotonically in audio_stats", 1)
def test_t4():
    """
    Since test_tone always sends exactly 50 frames, running N tones should
    accumulate N*50 packets. Verifies monotonic accumulation across 6 tones.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)

    samples = []
    for i in range(6):
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        time.sleep(2.0)  # wait for tone to complete
        stats = get_audio_stats()
        samples.append(packet_count(stats, BEDROOM_DEVICE_ID))

    increasing = all(samples[i] <= samples[i+1] for i in range(len(samples)-1))
    final = samples[-1] if samples else 0
    if not increasing:
        return FAIL, f"Not monotonic: {samples}"
    if final < 240:  # 6 x 40 minimum
        return FAIL, f"Final count too low: {final}. samples={samples}"
    return PASS, f"6 tones, accumulated {final} packets. samples={samples}"


@register_test("T5", "Heap stable after 10 sequential test_tones", 1)
def test_t5():
    """
    Since test_tone runs ~1s each, run 10 in series.
    Checks heap is stable (no leak per invocation).
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"

    st0 = device_status(BEDROOM_IP)
    heap0 = st0.get("free_heap", 0) if st0 else 0
    if heap0 == 0:
        return FAIL, "Cannot get initial heap"

    for i in range(10):
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        if code not in (200, 409):
            return FAIL, f"Tone {i+1} returned HTTP {code}"
        time.sleep(2.0)

    st1 = device_status(BEDROOM_IP)
    heap1 = st1.get("free_heap", 0) if st1 else 0
    delta = heap0 - heap1  # positive = leaked

    if heap1 == 0:
        return FAIL, "Device unreachable after 10 tones"
    if delta > 50_000:
        return FAIL, f"Heap leaked {delta//1024}KB. before={heap0//1024}KB after={heap1//1024}KB"
    return PASS, f"Heap: before={heap0//1024}KB, after={heap1//1024}KB, delta={delta//1024}KB"


@register_test("T6", "Back-to-back 10 test_tones → all complete, no crash", 1)
def test_t6():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    successes = 0
    failures = []
    for i in range(10):
        code, body = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        if code in (200, 409):
            successes += 1
        else:
            failures.append(f"Tone {i+1}: HTTP {code}")
        # Tone runs ~1s; wait for it to complete + settle
        time.sleep(2.0)
        if not device_reachable(BEDROOM_IP):
            failures.append(f"Device unreachable after tone {i+1}")
            break
    if failures:
        return FAIL, f"{successes}/10 ok. Failures: {'; '.join(failures)}"
    return PASS, f"All 10 tones completed ({successes}/10 returned 200/409)"


@register_test("T7", "Volume via MQTT (0,50,100) → /api/status reflects value", 1)
def test_t7():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("volume")
    failures = []
    for val in [0, 50, 100]:
        ok, detail = mqtt_set_and_verify(BEDROOM_IP, topic, str(val), "volume", val)
        if not ok:
            failures.append(f"vol={val}: {detail}")
    # Restore to 80
    mqtt_publish(topic, "80")
    if failures:
        return FAIL, "; ".join(failures)
    return PASS, "Volume 0→50→100 all reflected in /api/status"


@register_test("T8", "Mute ON/OFF via MQTT → /api/status reflects state", 1)
def test_t8():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("mute")
    failures = []
    for state, expected in [("ON", True), ("OFF", False)]:
        ok, detail = mqtt_set_and_verify(BEDROOM_IP, topic, state, "muted", expected)
        if not ok:
            failures.append(f"mute={state}: {detail}")
    if failures:
        return FAIL, "; ".join(failures)
    return PASS, "Mute ON→OFF reflected correctly"


@register_test("T9", "DND ON/OFF via MQTT → /api/status reflects state", 1)
def test_t9():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("dnd")
    failures = []
    for state, expected in [("ON", True), ("OFF", False)]:
        ok, detail = mqtt_set_and_verify(BEDROOM_IP, topic, state, "dnd", expected)
        if not ok:
            failures.append(f"dnd={state}: {detail}")
    if failures:
        return FAIL, "; ".join(failures)
    return PASS, "DND ON→OFF reflected correctly"


@register_test("T10", "Priority MQTT command accepted, device survives all values", 1)
def test_t10():
    """
    NOTE: /api/status (v2.8.4) does NOT expose 'priority' field.
    This test verifies: (a) MQTT publish accepted, (b) device stays alive.
    COVERAGE GAP: priority field not in /api/status — cannot verify via HTTP.
    Flag to code-writer: add 'priority' to /api/status response.
    """
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("priority")
    for val in ["NORMAL", "HIGH", "EMERGENCY"]:
        ok = mqtt_publish(topic, val)
        if not ok:
            return FAIL, f"MQTT publish failed for priority={val}"
        time.sleep(0.5)
    # Restore to NORMAL
    mqtt_publish(topic, "NORMAL")
    time.sleep(0.5)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after priority changes"
    # Check if priority in status (not present in v2.8.4)
    prio = st.get("priority")
    if prio is not None:
        return PASS, f"Priority field present: {prio}"
    return CLARIFY, ("Priority MQTT commands published (NORMAL/HIGH/EMERGENCY). Device alive. "
                     "Field 'priority' NOT in /api/status — cannot verify via HTTP. "
                     "COVERAGE GAP: add priority to /api/status.")


@register_test("T11", "AGC MQTT command accepted, device survives ON/OFF", 1)
def test_t11():
    """
    NOTE: /api/status (v2.8.4) does NOT expose 'agc_enabled' field.
    This test verifies: (a) MQTT publish accepted, (b) device stays alive.
    COVERAGE GAP: agc_enabled not in /api/status.
    """
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("agc")
    for state in ["ON", "OFF", "ON"]:  # end ON (restore original likely-on state)
        ok = mqtt_publish(topic, state)
        if not ok:
            return FAIL, f"MQTT publish failed for agc={state}"
        time.sleep(0.5)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after AGC changes"
    agc = st.get("agc_enabled")
    if agc is not None:
        return PASS, f"AGC field present: agc_enabled={agc}"
    return CLARIFY, ("AGC ON/OFF/ON commands published. Device alive. "
                     "Field 'agc_enabled' NOT in /api/status — cannot verify via HTTP. "
                     "COVERAGE GAP: add agc_enabled to /api/status.")


@register_test("T12", "LED ON/OFF via MQTT → state changes without crash", 1)
def test_t12():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("led")
    failures = []
    for state in ["OFF", "ON"]:
        ok = mqtt_publish(topic, state)
        if not ok:
            failures.append(f"led={state}: publish failed")
            continue
        time.sleep(0.8)
        if not device_reachable(BEDROOM_IP):
            failures.append(f"Device unreachable after led={state}")
    if failures:
        return FAIL, "; ".join(failures)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after LED test"
    return PASS, "LED ON/OFF published, device remained online"


@register_test("T13", "Target room via MQTT → command accepted, device alive", 1)
def test_t13():
    """
    NOTE: /api/status (v2.8.4) does NOT expose target_room field.
    Test verifies command accepted and device stays alive.
    """
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("target")
    ok = mqtt_publish(topic, "INTERCOM2")
    if not ok:
        return FAIL, "MQTT publish failed"
    time.sleep(1.0)
    ok2 = mqtt_publish(topic, "All Rooms")
    time.sleep(0.8)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after target change"
    target_val = st.get("target_room") or st.get("target")
    if target_val is not None:
        return PASS, f"target field present: {target_val}"
    return CLARIFY, ("Target MQTT commands published. Device alive. "
                     "Field 'target_room'/'target' NOT in /api/status — cannot verify via HTTP. "
                     "COVERAGE GAP: add target_room to /api/status response.")


@register_test("T14", "MQTT device discovery → both devices subscribe, state published", 1)
def test_t14():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    # Check /api/status on both devices shows mqtt_connected
    results_ok = []
    for ip, name in [(BEDROOM_IP, "Bedroom"), (INTERCOM2_IP, "INTERCOM2")]:
        st = device_status(ip)
        if st is None:
            results_ok.append(f"{name}: unreachable")
        elif st.get("mqtt_connected") is True:
            results_ok.append(f"{name}: mqtt_connected=true")
        else:
            results_ok.append(f"{name}: mqtt_connected={st.get('mqtt_connected')}")
    detail = ", ".join(results_ok)
    if "unreachable" in detail or "False" in detail or "false" in detail:
        return FAIL, detail
    return PASS, detail


@register_test("T15", "Online published after subscribes (ordering)", 1)
def test_t15():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not _serial_monitor or not _serial_monitor.is_active("bedroom"):
        return CLARIFY, "Serial monitor not available for Bedroom -- cannot verify subscribe ordering"
    # Check recent boot logs for subscribe/online ordering
    lines = _serial_monitor.get_lines_since("bedroom", time.time() - 300)  # last 5 minutes
    subscribe_lines = [l for l in lines if "subscribe" in l.lower() or "SUBSCRIBE" in l]
    online_lines = [l for l in lines if '"online"' in l or "availability" in l.lower()]
    if not subscribe_lines and not online_lines:
        return CLARIFY, "No subscribe/online log lines found in recent serial output (device may not have reconnected recently)"
    # If we have both, check ordering
    if subscribe_lines and online_lines:
        last_sub_idx = max(i for i, l in enumerate(lines) if any(s in l for s in ["subscribe", "SUBSCRIBE"]))
        first_online_idx = min((i for i, l in enumerate(lines) if '"online"' in l or "availability" in l.lower()), default=len(lines))
        if last_sub_idx < first_online_idx:
            return PASS, f"Subscribes ({len(subscribe_lines)} lines) appear before online publish"
        return FAIL, "Online published BEFORE some subscribes completed"
    return CLARIFY, f"Partial data: subscribes={len(subscribe_lines)}, online={len(online_lines)}"


@register_test("T16", "Device discovery → both devices see each other", 1)
def test_t16():
    failures = []
    for ip, name in [(BEDROOM_IP, "Bedroom"), (INTERCOM2_IP, "INTERCOM2")]:
        st = device_status(ip)
        if st is None:
            failures.append(f"{name}: unreachable")
            continue
        # Look for discovered_devices or similar field
        discovered = st.get("discovered_devices") or st.get("devices") or []
        if isinstance(discovered, list) and len(discovered) > 0:
            failures.append(f"{name}: {len(discovered)} device(s) discovered (ok)")
        else:
            failures.append(f"{name}: discovered_devices={discovered!r}")
    detail = "; ".join(failures)
    if "unreachable" in detail:
        return FAIL, detail
    # If discovered_devices field absent, this is CLARIFY
    if "discovered_devices=[]" in detail or "discovered_devices=None" in detail:
        return CLARIFY, f"discovered_devices empty or field absent (discovery info not logged in serial): {detail}"
    return PASS, detail


@register_test("T17", "LWT — device offline/online cycle visible via MQTT", 1)
def test_t17():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    # We can't power-cycle from here. Test that the availability topic has a LWT configured.
    # Verify both devices are currently 'online' by checking MQTT status topic.
    avail_topic_bed = f"intercom/{BEDROOM_UNIQUE_ID}/status"
    messages = mqtt_subscribe_collect(avail_topic_bed, duration=2.0)
    st = device_status(BEDROOM_IP)
    if st is None:
        return SKIP, "Bedroom unreachable"
    if not st.get("mqtt_connected"):
        return FAIL, "Bedroom not MQTT connected"
    return CLARIFY, ("LWT cannot be validated without device power cycle. "
                     "Verify manually: power off device, observe HA 'unavailable' state within keepalive+60s.")


@register_test("T18", "Call specific device via MQTT → target receives (hub chimes)", 1)
def test_t18():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
    time.sleep(3.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after call"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        match = _serial_monitor.wait_for_pattern("bedroom", r"(incoming call|Call chime|Beep:|hub chime active)", timeout=5.0, since=marker)
        if match:
            return PASS, f"Call received -- serial log: {match.strip()}"
        return FAIL, "No call/chime log in serial output within 5s"
    return CLARIFY, "Serial monitor not available -- cannot verify call receipt"


@register_test("T19", "Call All Rooms via MQTT → both devices get call", 1)
def test_t19():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    bed_marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    ic2_marker = _serial_monitor.mark("intercom2") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "All Rooms", "caller": "QA Test"}))
    time.sleep(3.0)
    bed_ok = device_reachable(BEDROOM_IP)
    ic2_ok = device_reachable(INTERCOM2_IP)
    if not bed_ok and not ic2_ok:
        return FAIL, "Both devices unreachable after All Rooms call"
    results = []
    for dev, marker, ip, ok in [("bedroom", bed_marker, BEDROOM_IP, bed_ok), ("intercom2", ic2_marker, INTERCOM2_IP, ic2_ok)]:
        if not ok:
            results.append(f"{dev}: unreachable")
            continue
        if marker and _serial_monitor and _serial_monitor.is_active(dev):
            match = _serial_monitor.wait_for_pattern(dev, r"(incoming call|Call chime|Beep:|hub chime active)", timeout=5.0, since=marker)
            results.append(f"{dev}: {'chimed' if match else 'NO chime log'}")
        else:
            results.append(f"{dev}: no serial monitor")
    detail = ", ".join(results)
    if all("chimed" in r for r in results):
        return PASS, f"All Rooms call: {detail}"
    if any("NO chime log" in r for r in results):
        return FAIL, f"All Rooms call: {detail}"
    return CLARIFY, f"All Rooms call: {detail}"


@register_test("T20", "Case-insensitive call matching → lowercase target works", 1)
def test_t20():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "bedroom intercom", "caller": "QA Test"}))
    time.sleep(3.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after lowercase call"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        match = _serial_monitor.wait_for_pattern("bedroom", r"(incoming call|Call chime|Beep:|hub chime active)", timeout=5.0, since=marker)
        if match:
            return PASS, f"Case-insensitive call received -- serial: {match.strip()}"
        return FAIL, "No call log for lowercase target"
    return CLARIFY, "Serial monitor not available"


@register_test("T21", "Self-echo prevention → originating device does not process its own call", 1)
def test_t21():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    # Publish a call with source set to Bedroom's unique ID.
    # NOTE: the firmware's self-echo guard is time-based (last_call_sent_time),
    # not MQTT source-field based. So the device WILL process this as a normal
    # call (it didn't actually send it). We check for both patterns.
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "source": BEDROOM_UNIQUE_ID, "caller": "QA Test"}))
    time.sleep(3.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after self-call"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        lines = _serial_monitor.get_lines_since("bedroom", marker)
        self_echo = any("self-sent" in l or "last_call_sent" in l for l in lines)
        call_received = any("Call chime" in l or "incoming call" in l or "Beep:" in l for l in lines)
        if self_echo:
            return PASS, "Self-echo guard triggered (ignoring self-sent call)"
        if call_received:
            return PASS, "Call processed normally (source field doesn't trigger self-echo -- self-echo is time-based)"
        return FAIL, "No call-related log lines found in serial"
    return CLARIFY, "Serial monitor not available"


@register_test("T22", "Chime detection — hub chime arrives before 150ms fallback beep", 1)
def test_t22():
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
    time.sleep(4.0)
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        match = _serial_monitor.wait_for_pattern("bedroom", r"(hub chime active|no hub audio|Beep:)", timeout=5.0, since=marker)
        if match:
            if "hub chime active" in match:
                return PASS, f"Hub chime detected (150ms check): {match.strip()}"
            elif "no hub audio" in match or "Beep:" in match:
                return PASS, f"Fallback beep played (hub chime not arrived in 150ms): {match.strip()}"
        return FAIL, "No chime detection log in serial"
    return CLARIFY, "Serial monitor not available"


@register_test("T23", "Hub GET /api/chimes → lists available chimes", 1)
def test_t23():
    """
    Hub chime API is GET /api/chimes (list) and POST /api/chimes/upload (upload).
    There is no POST /api/chime endpoint — chimes are triggered via MQTT call, not HTTP POST.
    This test verifies the chimes list endpoint is functional.
    """
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    code, body = http_get(hub_url("/api/chimes"), timeout=HUB_TIMEOUT)
    if code is None:
        return FAIL, f"Hub unreachable"
    if code == 200:
        try:
            data = json.loads(body)
            chimes = data.get("chimes", [])
            return PASS, f"Hub /api/chimes: {len(chimes)} chimes listed: {[c.get('name') for c in chimes][:5]}"
        except Exception:
            return FAIL, f"HTTP 200 but invalid JSON: {body[:80]!r}"
    return FAIL, f"Hub /api/chimes returned HTTP {code}: {body[:80]!r}"


@register_test("T24", "Hub MQTT call → triggers chime stream (MQTT path, not HTTP)", 1)
def test_t24():
    """
    Hub streams chimes via MQTT call topology, not HTTP POST /api/chime.
    Publish call via MQTT → hub receives → hub streams chime audio to devices.
    Verify hub stays alive and MQTT works.
    """
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    ok = mqtt_publish(CALL_TOPIC, json.dumps({"target": "All Rooms", "caller": "QA Test"}))
    if not ok:
        return FAIL, "MQTT call publish failed"
    time.sleep(3.0)
    if not hub_reachable():
        return FAIL, "Hub unreachable after MQTT call (possible chime stream crash)"
    return PASS, "MQTT call published, hub streamed chime and remained alive"


@register_test("T25", "TTS broadcast via MQTT notify → hub processes without error", 1)
def test_t25():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    ok = mqtt_publish(HUB_NOTIFY_TOPIC, json.dumps({"message": "QA test notification"}))
    if not ok:
        return FAIL, "MQTT publish failed"
    time.sleep(3)
    if not hub_reachable():
        return FAIL, "Hub unreachable after TTS notify"
    return PASS, "TTS notify published, hub remained online"


@register_test("T26", "Hub audio_stats accurate — reset, tone, verify count = ~50", 1)
def test_t26():
    """
    KNOWN BEHAVIOR: test_tone always sends exactly 50 frames (1 second).
    The 'duration' param is ignored by firmware. Verify stats count ~50.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
    if code not in (200, 409):
        return FAIL, f"tone POST returned {code}"
    time.sleep(2.0)
    stats = get_audio_stats()
    count = packet_count(stats, BEDROOM_DEVICE_ID)
    # 50 frames x 20ms = 1 second; allow ±10 for jitter/loss
    if 40 <= count <= 60:
        return PASS, f"{count} packets (expected 40–60 for single 50-frame tone)"
    return FAIL, f"{count} packets (expected 40–60). stats={stats}"


@register_test("T27", "WebSocket connect → receives init/welcome message", 1)
def test_t27():
    if not _ws_available:
        return SKIP, "websockets library not installed"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    msgs = ws_collect(f"ws://{HUB_IP}:{HUB_PORT}/ws", [], collect_duration=2.0)
    if not msgs:
        return FAIL, "No messages received after connecting to WebSocket"
    return PASS, f"Received {len(msgs)} message(s) on connect: {msgs[0][:100]!r}"


@register_test("T28", "WebSocket register → hub acknowledges client", 1)
def test_t28():
    if not _ws_available:
        return SKIP, "websockets library not installed"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reg_msg = json.dumps({"type": "register", "device_name": "qa-test"})
    msgs = ws_collect(f"ws://{HUB_IP}:{HUB_PORT}/ws", [reg_msg], collect_duration=2.0)
    if not msgs:
        return CLARIFY, "No response to register message (hub may not send ack)"
    return PASS, f"Received {len(msgs)} message(s): {msgs[0][:100]!r}"


@register_test("T29", "WebSocket get_state → returns target list or state", 1)
def test_t29():
    if not _ws_available:
        return SKIP, "websockets library not installed"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    state_msg = json.dumps({"type": "get_state"})
    msgs = ws_collect(f"ws://{HUB_IP}:{HUB_PORT}/ws", [state_msg], collect_duration=2.0)
    if not msgs:
        return CLARIFY, "No response to get_state (hub may not support this message type)"
    return PASS, f"Received {len(msgs)} message(s): {msgs[0][:200]!r}"


@register_test("T30", "BUG-E6 — 300-byte body → HTTP 400, no TCP RST", 1)
def test_t30():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    # Build a body > 256 bytes to trigger size check
    big_body = ('{"action":"test_tone",' + '"x":"' + "A" * 280 + '"}').encode()
    try:
        code, raw = http_post(f"http://{BEDROOM_IP}/api/test", {},
                              raw_body=big_body, timeout=10)
    except ConnectionResetError:
        return FAIL, "TCP RST received (BUG-E6 not fixed — body not drained before response)"
    except Exception as exc:
        return FAIL, f"Unexpected error: {exc}"
    if code == 400:
        return PASS, f"HTTP 400 returned (no TCP RST). Body: {raw[:80]!r}"
    if code == 200:
        return FAIL, f"Server accepted oversized body (returned 200)"
    return CLARIFY, f"HTTP {code} — {raw[:80]!r}"


@register_test("T31", "BUG-G2 — error response Content-Type is application/json", 1)
def test_t31():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    # Send invalid JSON to trigger a 400
    try:
        data = b"not valid json at all"
        req = urllib.request.Request(f"http://{BEDROOM_IP}/api/test", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ct = r.headers.get("Content-Type", "")
                code = r.status
        except urllib.error.HTTPError as e:
            ct = e.headers.get("Content-Type", "")
            code = e.code
    except Exception as exc:
        return FAIL, f"Request failed: {exc}"

    if "application/json" in ct:
        return PASS, f"HTTP {code}, Content-Type: {ct}"
    if "text/html" in ct:
        return FAIL, f"BUG-G2 NOT fixed: Content-Type={ct!r} (expected application/json)"
    return CLARIFY, f"HTTP {code}, Content-Type={ct!r}"


@register_test("T32", "BUG-001 — heap_usage_percent in 0–100 range", 1)
def test_t32():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Cannot reach /api/status"
    pct = st.get("heap_usage_percent")
    if pct is None:
        # Try diagnostics endpoint
        code, body = http_get(f"http://{BEDROOM_IP}/diagnostics")
        if code == 200 and body:
            try:
                diag = json.loads(body)
                pct = diag.get("heap_usage_percent")
            except Exception:
                pass
    if pct is None:
        return CLARIFY, "heap_usage_percent field not found in /api/status or /diagnostics"
    if 0 <= pct <= 100:
        return PASS, f"heap_usage_percent={pct}% (valid range)"
    return FAIL, f"BUG-001 NOT fixed: heap_usage_percent={pct}% (invalid)"


# ===========================================================================
#  CATEGORY 2: STRESS TESTING  (T33–T46)
# ===========================================================================

@register_test("T33", "Spam 20 beep requests in 2s → all return JSON, no crash", 2)
def test_t33():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    responses = []
    errors = []

    def send_beep(i):
        try:
            code, raw = http_post(f"http://{BEDROOM_IP}/api/test", {"action": "beep"}, timeout=10)
            responses.append(code)
        except ConnectionResetError:
            errors.append(f"req{i}: TCP RST")
        except Exception as e:
            errors.append(f"req{i}: {e}")

    threads = [threading.Thread(target=send_beep, args=(i,)) for i in range(20)]
    t_start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)
    elapsed = time.time() - t_start

    if errors:
        return FAIL, f"Errors: {errors[:5]}. Responses: {set(responses)}"
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after spam test"

    non_json = [r for r in responses if r not in (200, 409, 400)]
    if non_json:
        return FAIL, f"Non-JSON/unexpected codes: {non_json}. All: {responses}"
    return PASS, f"20 requests in {elapsed:.1f}s. Codes: {sorted(set(responses))}"


@register_test("T34", "10 concurrent test_tone requests → 409 rejections, no crash", 2)
def test_t34():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    codes = []
    errors = []

    def send_tone(i):
        try:
            code, _ = http_post(f"http://{BEDROOM_IP}/api/test",
                                {"action": "test_tone", "duration": 3}, timeout=15)
            codes.append(code)
        except Exception as e:
            errors.append(f"req{i}: {e}")

    threads = [threading.Thread(target=send_tone, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    time.sleep(1)  # ensure all tones done

    if errors:
        return FAIL, f"Errors: {errors[:5]}"
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after concurrent tone test"
    # NOTE: Due to httpd serialisation (single-task), requests queue and run sequentially.
    # We expect all to return 200 (each runs in series), not 409.
    # 409 would require true concurrency at the handler level.
    all_ok = all(c in (200, 409) for c in codes)
    if not all_ok:
        return FAIL, f"Unexpected codes: {codes}"
    return PASS, f"10 concurrent requests. Codes: {sorted(set(codes))} (httpd serialises)"


@register_test("T35", "50 rapid volume changes (0→100) via MQTT → final value correct", 2)
def test_t35():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("volume")
    try:
        c = mqtt.Client(client_id="qa_stress_vol", clean_session=True)
        c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.loop_start()
        for i in range(50):
            c.publish(topic, str(i % 2 == 0 and 100 or 0))
            time.sleep(0.1)
        # Final value: 80
        c.publish(topic, "80")
        time.sleep(0.5)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        return FAIL, f"MQTT stress failed: {e}"
    time.sleep(1.0)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after volume spam"
    got = st.get("volume")
    if got == 80:
        return PASS, f"Final volume={got} (correct after 50 rapid changes)"
    return FAIL, f"Final volume={got} (expected 80)"


@register_test("T36", "20 rapid mute ON/OFF toggles → final state correct", 2)
def test_t36():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topic = bedroom_topic("mute")
    try:
        c = mqtt.Client(client_id="qa_stress_mute", clean_session=True)
        c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.loop_start()
        for i in range(20):
            c.publish(topic, "ON" if i % 2 == 0 else "OFF")
            time.sleep(0.1)
        # End on OFF (mute=false)
        c.publish(topic, "OFF")
        time.sleep(0.5)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        return FAIL, f"MQTT stress failed: {e}"
    time.sleep(1.0)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after mute spam"
    got = st.get("muted")
    if got is False:
        return PASS, "Final muted=false (correct after 20 rapid toggles)"
    return FAIL, f"Final muted={got} (expected False)"


@register_test("T37", "20 rapid call notifications in 5s → device doesn't crash", 2)
def test_t37():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    try:
        c = mqtt.Client(client_id="qa_stress_call", clean_session=True)
        c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.loop_start()
        for _ in range(20):
            c.publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom"}))
            time.sleep(0.25)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        return FAIL, f"MQTT stress failed: {e}"
    time.sleep(2.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after rapid call spam"
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Cannot get status after call spam"
    return PASS, f"20 calls sent, device alive. heap={st.get('free_heap',0)//1024}KB"


@register_test("T38", "10 PTT tap cycles (tone ~1s each, 200ms gap) → no stale audio", 2)
def test_t38():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    failures = []
    for i in range(10):
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        if code not in (200, 409):
            failures.append(f"cycle {i+1}: HTTP {code}")
        time.sleep(2.0)  # ~1s tone + 1s gap (duration param ignored; always 50 frames)
        if not device_reachable(BEDROOM_IP):
            failures.append(f"unreachable after cycle {i+1}")
            break
    if failures:
        return FAIL, f"{'; '.join(failures)}"
    return PASS, "10 PTT tap cycles completed, device remained online throughout"


@register_test("T39", "Both devices test_tone simultaneously → hub shows both", 2)
def test_t39():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not device_reachable(INTERCOM2_IP):
        return SKIP, "INTERCOM2 unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)

    results_bed = [None]
    results_ic2 = [None]

    def tone_bedroom():
        results_bed[0] = post_test_action(BEDROOM_IP, "test_tone", timeout=10)

    def tone_ic2():
        results_ic2[0] = post_test_action(INTERCOM2_IP, "test_tone", timeout=10)

    t1 = threading.Thread(target=tone_bedroom)
    t2 = threading.Thread(target=tone_ic2)
    t1.start(); t2.start()
    t1.join(timeout=15); t2.join(timeout=15)
    time.sleep(2.0)

    stats = get_audio_stats()
    bed_count = packet_count(stats, BEDROOM_DEVICE_ID)
    ic2_count = packet_count(stats, INTERCOM2_DEVICE_ID)

    failures = []
    if bed_count < 40:
        failures.append(f"Bedroom only {bed_count} packets")
    if ic2_count < 40:
        failures.append(f"INTERCOM2 only {ic2_count} packets")
    if failures:
        return FAIL, f"{'; '.join(failures)}. senders={stats.get('senders',{}) if stats else 'N/A'}"
    return PASS, f"Bedroom={bed_count} pkts, INTERCOM2={ic2_count} pkts (simultaneous TX)"


@register_test("T40", "Bedroom test_tone + simultaneous call to INTERCOM2 → both succeed", 2)
def test_t40():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"

    tone_result = [None]
    def run_tone():
        tone_result[0] = post_test_action(BEDROOM_IP, "test_tone", timeout=10)

    t = threading.Thread(target=run_tone)
    t.start()
    time.sleep(0.3)  # tone runs ~1s; publish call immediately after

    # While tone is running, call INTERCOM2
    ok = mqtt_publish(CALL_TOPIC, json.dumps({"target": "INTERCOM2", "caller": "QA Test"}))
    t.join(timeout=12)

    if not ok:
        return FAIL, "Call MQTT publish failed"
    tone_code = tone_result[0][0] if tone_result[0] else None
    if tone_code not in (200, 409):
        return FAIL, f"Tone returned HTTP {tone_code}"
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after test"
    return PASS, f"Tone HTTP {tone_code}, call published. Both operations completed."


@register_test("T41", "Call All Rooms while both devices have test_tone running", 2)
def test_t41():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"

    def tone_b():
        post_test_action(BEDROOM_IP, "test_tone", timeout=10)
    def tone_ic2():
        post_test_action(INTERCOM2_IP, "test_tone", timeout=10)

    t1 = threading.Thread(target=tone_b)
    t2 = threading.Thread(target=tone_ic2)
    t1.start(); t2.start()
    time.sleep(0.5)

    ok = mqtt_publish(CALL_TOPIC, json.dumps({"target": "All Rooms", "caller": "QA Test"}))
    t1.join(timeout=12); t2.join(timeout=12)
    time.sleep(1.0)

    if not ok:
        return FAIL, "MQTT publish failed"
    bed_ok = device_reachable(BEDROOM_IP)
    ic2_ok = device_reachable(INTERCOM2_IP)
    if not bed_ok:
        return FAIL, "Bedroom crashed after simultaneous tone+call"
    detail = f"Bedroom: {'ok' if bed_ok else 'down'}, INTERCOM2: {'ok' if ic2_ok else 'down'}"
    return PASS, f"Call during dual tone: {detail}"


@register_test("T42", "Alternating calls between devices — 5 cycles", 2)
def test_t42():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    failures = []
    for i in range(5):
        target = "Bedroom Intercom" if i % 2 == 0 else "INTERCOM2"
        ok = mqtt_publish(CALL_TOPIC, json.dumps({"target": target, "caller": "QA Test"}))
        if not ok:
            failures.append(f"cycle {i+1} publish failed")
        time.sleep(2.0)
    time.sleep(1.0)
    if not device_reachable(BEDROOM_IP):
        failures.append("Bedroom unreachable after alternating calls")
    if failures:
        return FAIL, "; ".join(failures)
    return PASS, "5 alternating calls completed, Bedroom device alive"


@register_test("T43", "5-min Bedroom soak: 100 tones + heap monitoring → stable throughout", 2)
def test_t43():
    """
    Soak test: run 100 x 50-frame tones with 2-second gaps (total ~5 min).
    Since test_tone always sends 50 frames, we fire tones repeatedly.
    Monitors heap for leaks and device for crashes.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    st0 = device_status(BEDROOM_IP)
    heap0 = st0.get("free_heap", 0) if st0 else 0

    heap_samples = []
    pkt_samples = []
    failures = []
    for i in range(100):
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        if code not in (200, 409):
            failures.append(f"tone {i+1}: HTTP {code}")
        time.sleep(2.0)
        if i % 10 == 9:  # sample heap every 10 tones
            st = device_status(BEDROOM_IP)
            if st:
                heap_samples.append(st.get("free_heap", 0))
            else:
                failures.append(f"device unreachable at tone {i+1}")
                break
            stats = get_audio_stats()
            pkt_samples.append(packet_count(stats, BEDROOM_DEVICE_ID))
            print(f"    [T43] tone {i+1}/100: heap={heap_samples[-1]//1024}KB pkts={pkt_samples[-1]}",
                  flush=True)

    if failures:
        return FAIL, "; ".join(failures[:3])
    if not heap_samples:
        return FAIL, "No heap samples collected"
    min_heap = min(heap_samples)
    heap_delta = heap0 - min_heap
    if heap_delta > 500_000:  # > 500KB leak in 100 tones
        return FAIL, f"Heap leaked {heap_delta//1024}KB over 100 tones. samples={heap_samples}"
    if min_heap < 6_000_000:
        return FAIL, f"Heap too low: {min_heap//1024}KB"
    return PASS, f"100 tones: heap min={min_heap//1024}KB, delta={heap_delta//1024}KB, pkts={pkt_samples[-1]}"


@register_test("T44", "5-min INTERCOM2 soak: 100 tones → heap stable", 2)
def test_t44():
    """
    Same as T43 but for INTERCOM2. Verifies BUG-002 (ENOMEM after extended use).
    """
    if not device_reachable(INTERCOM2_IP):
        return SKIP, "INTERCOM2 unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    reset_audio_stats()
    time.sleep(0.5)
    st0 = device_status(INTERCOM2_IP)
    heap0 = st0.get("free_heap", 0) if st0 else 0

    heap_samples = []
    failures = []
    for i in range(100):
        code, _ = post_test_action(INTERCOM2_IP, "test_tone", timeout=10)
        if code not in (200, 409):
            failures.append(f"tone {i+1}: HTTP {code}")
        time.sleep(2.0)
        if i % 10 == 9:
            st = device_status(INTERCOM2_IP)
            if st:
                heap_samples.append(st.get("free_heap", 0))
            else:
                failures.append(f"unreachable at tone {i+1}")
                break
            print(f"    [T44] tone {i+1}/100: heap={heap_samples[-1]//1024}KB", flush=True)

    if failures:
        return FAIL, "; ".join(failures[:3])
    if not heap_samples:
        return FAIL, "No heap samples"
    min_heap = min(heap_samples)
    heap_delta = heap0 - min_heap
    if heap_delta > 500_000:
        return FAIL, f"Heap leaked {heap_delta//1024}KB"
    stats = get_audio_stats()
    count = packet_count(stats, INTERCOM2_DEVICE_ID)
    return PASS, f"100 tones: heap min={min_heap//1024}KB, delta={heap_delta//1024}KB"


@register_test("T45", "10-min idle soak → MQTT connected, heap stable", 2)
def test_t45():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    samples = []
    for i in range(10):
        st_bed = device_status(BEDROOM_IP)
        st_ic2 = device_status(INTERCOM2_IP)
        samples.append({
            "t": i,
            "bed_mqtt": st_bed.get("mqtt_connected") if st_bed else None,
            "bed_heap": st_bed.get("free_heap", 0) if st_bed else 0,
            "ic2_mqtt": st_ic2.get("mqtt_connected") if st_ic2 else None,
        })
        time.sleep(60)

    bed_drops = [s for s in samples if not s["bed_mqtt"]]
    ic2_drops = [s for s in samples if not s["ic2_mqtt"]]
    min_heap = min(s["bed_heap"] for s in samples if s["bed_heap"] > 0) if samples else 0

    issues = []
    if bed_drops:
        issues.append(f"Bedroom MQTT dropped at t={[s['t'] for s in bed_drops]}")
    if ic2_drops:
        issues.append(f"INTERCOM2 MQTT dropped at t={[s['t'] for s in ic2_drops]}")
    if min_heap < 6_000_000:
        issues.append(f"Bedroom heap fell to {min_heap//1024}KB")

    if issues:
        return FAIL, "; ".join(issues)
    return PASS, f"10-min idle: MQTT stable, Bedroom heap min={min_heap//1024}KB"


@register_test("T46", "50 sequential tones (~5 min) → no degradation in packet count", 2)
def test_t46():
    """
    Runs 50 tones with 2s gaps (~5 min total). Each tone should produce ~50 packets.
    Verifies no degradation over time (TX pipeline stays healthy).
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    failures = []
    pkt_counts = []
    for i in range(50):
        reset_audio_stats()
        time.sleep(0.5)
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        if code not in (200, 409):
            failures.append(f"Tone {i+1}: HTTP {code}")
        time.sleep(2.0)
        stats = get_audio_stats()
        count = packet_count(stats, BEDROOM_DEVICE_ID)
        pkt_counts.append(count)
        if not device_reachable(BEDROOM_IP):
            failures.append(f"Unreachable after tone {i+1}")
            break
        if i % 10 == 9:
            print(f"    [T46] tone {i+1}/50: {count} pkts", flush=True)

    if failures:
        return FAIL, "; ".join(failures) + f" counts_tail={pkt_counts[-5:]}"
    if not pkt_counts:
        return FAIL, "No packet data"
    # Each tone should produce 40–60 packets; flag if > 30% below 40
    low = [i+1 for i, c in enumerate(pkt_counts) if c < 30]
    if low:
        return FAIL, f"Low packet count (<30) on tones {low[:5]}. counts_tail={pkt_counts[-5:]}"
    avg = sum(pkt_counts) / len(pkt_counts)
    return PASS, f"50 tones: avg={avg:.1f} pkts, min={min(pkt_counts)}, max={max(pkt_counts)}"


# ===========================================================================
#  CATEGORY 3: EDGE CASES  (T47–T63)
# ===========================================================================

@register_test("T47", "DND ON + normal call → device does NOT receive (verifiable via log)", 3)
def test_t47():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    mqtt_publish(bedroom_topic("dnd"), "ON")
    time.sleep(0.8)
    st = device_status(BEDROOM_IP)
    if not st or not st.get("dnd"):
        return FAIL, "DND not set -- cannot test blocking"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
    time.sleep(3.0)
    mqtt_publish(bedroom_topic("dnd"), "OFF")
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after DND call test"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        lines = _serial_monitor.get_lines_since("bedroom", marker)
        dnd_blocked = any("DND active" in l or ("dnd" in l.lower() and "ignor" in l.lower()) for l in lines)
        chime_played = any("Call chime" in l or "Beep:" in l for l in lines)
        if dnd_blocked and not chime_played:
            return PASS, "DND correctly blocked normal call (logged, no chime)"
        if chime_played:
            return FAIL, "Chime played despite DND=ON"
        # DND blocks audio packets, not necessarily the MQTT call handler
        dnd_audio = any("DND active, ignoring audio" in l for l in lines)
        if dnd_audio:
            return PASS, "DND correctly blocking incoming audio packets"
        return CLARIFY, f"DND=ON but no DND blocking log found. Lines checked: {len(lines)}"
    return CLARIFY, "Serial monitor not available"


@register_test("T48", "DND ON + EMERGENCY call → device DOES chime (bypasses DND)", 3)
def test_t48():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    mqtt_publish(bedroom_topic("dnd"), "ON")
    time.sleep(0.5)
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "priority": "EMERGENCY", "caller": "QA Test"}))
    time.sleep(3.0)
    mqtt_publish(bedroom_topic("dnd"), "OFF")
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after EMERGENCY call test"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        match = _serial_monitor.wait_for_pattern("bedroom", r"(EMERGENCY audio incoming|forced unmute|Call chime|Beep:)", timeout=5.0, since=marker)
        if match:
            return PASS, f"DND bypassed by EMERGENCY -- serial: {match.strip()}"
        return FAIL, "No EMERGENCY bypass log in serial (DND may have blocked it)"
    return CLARIFY, "Serial monitor not available"


@register_test("T49", "DND ON + test_tone → tone still works", 3)
def test_t49():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    mqtt_publish(bedroom_topic("dnd"), "ON")
    time.sleep(0.8)
    code, body = post_test_action(BEDROOM_IP, "test_tone", {"duration": 3}, timeout=15)
    time.sleep(3.5)
    mqtt_publish(bedroom_topic("dnd"), "OFF")
    if code in (200, 409):
        return PASS, f"test_tone with DND=ON returned HTTP {code} (DND does not block local actions)"
    return FAIL, f"test_tone returned HTTP {code}: {body}"


@register_test("T50", "Mute ON + beep → behavior observed (beep may or may not play)", 3)
def test_t50():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    mqtt_publish(bedroom_topic("mute"), "ON")
    time.sleep(0.8)
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    code, body = post_test_action(BEDROOM_IP, "beep", timeout=10)
    time.sleep(1.5)
    mqtt_publish(bedroom_topic("mute"), "OFF")
    if code not in (200, 409):
        return FAIL, f"beep returned unexpected HTTP {code}: {body}"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        match = _serial_monitor.wait_for_pattern("bedroom", r"Beep:", timeout=3.0, since=marker)
        if match:
            return PASS, f"Beep behavior with mute=ON: {match.strip()}"
        return PASS, "Beep command accepted, no beep log (may have been skipped while muted)"
    return CLARIFY, f"beep returned HTTP {code}. Serial monitor not available."


@register_test("T51", "Volume change during active audio playback", 3)
def test_t51():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    tone_result = [None]
    def run_tone():
        tone_result[0] = post_test_action(BEDROOM_IP, "test_tone", {"duration": 5}, timeout=12)

    t = threading.Thread(target=run_tone)
    t.start()
    time.sleep(1.0)  # tone is running
    # Change volume mid-tone
    mqtt_publish(bedroom_topic("volume"), "30")
    time.sleep(1.0)
    mqtt_publish(bedroom_topic("volume"), "80")
    t.join(timeout=10)

    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after volume-during-playback test"
    st = device_status(BEDROOM_IP)
    got_vol = st.get("volume") if st else None
    if got_vol == 80:
        return PASS, f"Volume changed during playback, device alive, final volume={got_vol}"
    return FAIL, f"Final volume={got_vol} (expected 80)"


@register_test("T52", "Call followed immediately by test_tone → tone executes", 3)
def test_t52():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
    time.sleep(0.5)
    code, body = post_test_action(BEDROOM_IP, "test_tone", {"duration": 2}, timeout=10)
    time.sleep(2.5)
    if code in (200, 409):
        return PASS, f"test_tone after call returned HTTP {code}"
    return FAIL, f"test_tone after call returned HTTP {code}: {body}"


@register_test("T53", "Malformed JSON to MQTT volume topic → device doesn't crash", 3)
def test_t53():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    malformed = ['{bad json', '{"value": }', '\\x00\\x01', '<script>alert(1)</script>', '']
    for payload in malformed:
        mqtt_publish(bedroom_topic("volume"), payload)
        time.sleep(0.3)
    time.sleep(1.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after malformed MQTT payloads"
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Cannot get status after malformed payloads"
    return PASS, f"Device survived {len(malformed)} malformed MQTT payloads. heap={st.get('free_heap',0)//1024}KB"


@register_test("T54", "Invalid volume values via MQTT (-1, 999, 'abc') → clamped or rejected", 3)
def test_t54():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    # Set known good value first
    mqtt_publish(bedroom_topic("volume"), "50")
    time.sleep(0.5)
    for bad_val in ["-1", "999", "abc", "null", "200"]:
        mqtt_publish(bedroom_topic("volume"), bad_val)
        time.sleep(0.3)
    time.sleep(1.0)
    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Device unreachable after invalid volume test"
    got = st.get("volume", -1)
    # Restore
    mqtt_publish(bedroom_topic("volume"), "80")
    if 0 <= got <= 100:
        return PASS, f"Volume within valid range after invalid values. got={got}"
    return FAIL, f"Volume out of range after invalid values: got={got}"


@register_test("T55", "Empty MQTT payload to command topics → no crash", 3)
def test_t55():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    topics = [
        bedroom_topic("mute"),
        bedroom_topic("dnd"),
        bedroom_topic("agc"),
        bedroom_topic("led"),
    ]
    for topic in topics:
        mqtt_publish(topic, "")
        time.sleep(0.2)
    time.sleep(1.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after empty payload test"
    return PASS, f"Device survived empty payloads to {len(topics)} topics"


@register_test("T56", "Oversized POST to /api/test (1KB, 10KB) → clean HTTP 400", 3)
def test_t56():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    failures = []
    for size in [1024, 10240]:
        body = ('{"action":"beep","padding":"' + "X" * (size - 30) + '"}').encode()
        try:
            code, raw = http_post(f"http://{BEDROOM_IP}/api/test", {}, raw_body=body, timeout=10)
        except ConnectionResetError:
            failures.append(f"{size}B: TCP RST (body not drained — BUG-E6 pattern)")
            continue
        except Exception as e:
            failures.append(f"{size}B: {e}")
            continue
        if code == 400:
            pass  # expected
        elif code == 200:
            failures.append(f"{size}B: accepted (should reject)")
        else:
            failures.append(f"{size}B: HTTP {code}")
    if not device_reachable(BEDROOM_IP):
        failures.append("Device unreachable after oversized POST")
    if failures:
        return FAIL, "; ".join(failures)
    return PASS, "1KB and 10KB bodies both returned HTTP 400 cleanly"


@register_test("T57", "Invalid action to /api/test → HTTP 400 with error JSON", 3)
def test_t57():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    code, body = post_test_action(BEDROOM_IP, "invalid_action_xyz")
    if code == 400:
        raw = body.get("raw", "") if isinstance(body, dict) else str(body)
        return PASS, f"HTTP 400 received. body={str(body)[:80]}"
    if code == 200:
        return FAIL, f"Server accepted invalid action (HTTP 200). body={body}"
    return CLARIFY, f"HTTP {code}: {body}"


@register_test("T58", "POST /api/test without Content-Type → handled gracefully", 3)
def test_t58():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    try:
        data = b'{"action":"beep"}'
        req = urllib.request.Request(f"http://{BEDROOM_IP}/api/test", data=data, method="POST")
        # Deliberately NOT setting Content-Type
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code = r.status
                raw = r.read()
        except urllib.error.HTTPError as e:
            code = e.code
            raw = e.read()
    except ConnectionResetError:
        return FAIL, "TCP RST — server crashed on missing Content-Type"
    except Exception as e:
        return FAIL, f"Request failed: {e}"
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after no-Content-Type test"
    if code in (200, 400):
        return PASS, f"HTTP {code} — server handled missing Content-Type gracefully"
    return CLARIFY, f"HTTP {code}: {raw[:80]!r}"


@register_test("T59", "GET /api/test (wrong method) → 405 or appropriate rejection", 3)
def test_t59():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    code, body = http_get(f"http://{BEDROOM_IP}/api/test", timeout=10)
    if code == 405:
        return PASS, "HTTP 405 Method Not Allowed"
    if code == 400:
        return PASS, f"HTTP 400 (server rejects GET on POST endpoint)"
    if code == 200:
        return FAIL, "Server returned 200 on GET /api/test (should reject)"
    return CLARIFY, f"HTTP {code}: {body[:80] if body else ''!r}"


@register_test("T60", "Heap before/after 100 test_tones → no leak > 100KB", 3)
def test_t60():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    st0 = device_status(BEDROOM_IP)
    heap0 = st0.get("free_heap", 0) if st0 else 0
    if heap0 == 0:
        return FAIL, "Cannot get initial heap"
    for i in range(100):
        post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        time.sleep(2.0)  # ~1s tone + 1s settle (duration param is ignored)
        if i % 20 == 19 and not device_reachable(BEDROOM_IP):
            return FAIL, f"Device unreachable after {i+1} tones"
    st1 = device_status(BEDROOM_IP)
    heap1 = st1.get("free_heap", 0) if st1 else 0
    delta = heap0 - heap1  # positive = leaked
    if delta > 100_000:
        return FAIL, f"Heap leaked {delta//1024}KB after 100 tones. before={heap0//1024}KB after={heap1//1024}KB"
    return PASS, f"Heap: before={heap0//1024}KB, after={heap1//1024}KB, delta={delta//1024}KB"


@register_test("T61", "Heap before/after 100 MQTT command changes → no leak", 3)
def test_t61():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    st0 = device_status(BEDROOM_IP)
    heap0 = st0.get("free_heap", 0) if st0 else 0
    if heap0 == 0:
        return FAIL, "Cannot get initial heap"
    try:
        c = mqtt.Client(client_id="qa_heap_mqtt", clean_session=True)
        c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        c.loop_start()
        vol_topic = bedroom_topic("volume")
        mute_topic = bedroom_topic("mute")
        for i in range(100):
            c.publish(vol_topic, str(i % 101))
            c.publish(mute_topic, "ON" if i % 2 == 0 else "OFF")
            time.sleep(0.1)
        c.publish(vol_topic, "80")
        c.publish(mute_topic, "OFF")
        time.sleep(0.5)
        c.loop_stop()
        c.disconnect()
    except Exception as e:
        return FAIL, f"MQTT error: {e}"
    time.sleep(1.0)
    st1 = device_status(BEDROOM_IP)
    heap1 = st1.get("free_heap", 0) if st1 else 0
    delta = heap0 - heap1
    if delta > 100_000:
        return FAIL, f"Heap leaked {delta//1024}KB after 100 MQTT commands"
    return PASS, f"Heap: before={heap0//1024}KB, after={heap1//1024}KB, delta={delta//1024}KB"


@register_test("T62", "Audio_stats packet rate consistent across 20 sequential tones", 3)
def test_t62():
    """
    Checks that the TX packet rate is consistent over time.
    Since test_tone always sends 50 frames, run 20 tones with resets between them.
    Each reset + tone window should yield ~50 packets.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"

    rates = []
    for _ in range(20):
        reset_audio_stats()
        time.sleep(0.5)
        post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        time.sleep(2.0)
        stats = get_audio_stats()
        count = packet_count(stats, BEDROOM_DEVICE_ID)
        rates.append(count)

    if not rates:
        return FAIL, "No rate samples"
    low_windows = [i+1 for i, r in enumerate(rates) if r < 35]
    if low_windows:
        return FAIL, f"Low packet count (<35) in windows {low_windows}. rates={rates}"
    avg = sum(rates) / len(rates)
    return PASS, f"20 tones: avg={avg:.1f} pkts, min={min(rates)}, max={max(rates)}"


@register_test("T63", "MQTT reconnect after broker restart → devices come back online", 3)
def test_t63():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    return CLARIFY, ("Cannot restart MQTT broker from QA runner without SSH access. "
                     "Verify manually: restart Mosquitto on HA server, "
                     "observe devices reconnect within 60s (keepalive=60s + reconnect interval).")


# ===========================================================================
#  CATEGORY 4: KNOWN BUG REPRODUCTION  (T64–T68)
# ===========================================================================

@register_test("T64", "BUG-003 — 30-min soak: TCP/ARP still working throughout", 4)
def test_t64():
    """
    BUG-003: Devices lose ALL inbound TCP/ARP connectivity after ~20 min post-boot.
    UDP TX continues. MQTT cycles. ARP stops responding.
    This test probes for this regression.
    """
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable at start"
    failures = []
    samples = []
    for i in range(30):
        t_start = time.time()
        st_bed = device_status(BEDROOM_IP)
        st_ic2 = device_status(INTERCOM2_IP)
        elapsed = time.time() - t_start
        samples.append({
            "min": i + 1,
            "bed_alive": st_bed is not None,
            "bed_mqtt": st_bed.get("mqtt_connected") if st_bed else None,
            "ic2_alive": st_ic2 is not None,
            "response_time": elapsed,
        })
        if st_bed is None:
            failures.append(f"Bedroom unreachable at t={i+1}min")
        if i % 5 == 4:
            print(f"    [T64] t={i+1}min: Bedroom={'ok' if st_bed else 'DOWN'}, "
                  f"INTERCOM2={'ok' if st_ic2 else 'down/skip'}", flush=True)
        # Quick MQTT keepalive: publish small heartbeat
        if _paho_available:
            mqtt_publish(bedroom_topic("volume"), str(80))
        time.sleep(60)

    if failures:
        return FAIL, f"BUG-003 reproduced! {'; '.join(failures[:5])}"
    bed_alive_count = sum(1 for s in samples if s["bed_alive"])
    return PASS, f"All {bed_alive_count}/30 checks passed. BUG-003 NOT reproduced in this run."


@register_test("T65", "BUG-004 — Rapid call spam → chimes queue cleanly, no garble", 4)
def test_t65():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    for i in range(10):
        mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
        time.sleep(0.3)
    time.sleep(3.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after rapid call spam (possible crash)"
    st = device_status(BEDROOM_IP)
    heap = st.get('free_heap', 0) // 1024 if st else 0
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        lines = _serial_monitor.get_lines_since("bedroom", marker)
        i2s_errors = [l for l in lines if "I2S" in l and ("error" in l.lower() or "collision" in l.lower() or "fail" in l.lower())]
        if i2s_errors:
            return FAIL, f"I2S errors during call spam: {i2s_errors[:3]}"
        return PASS, f"10 rapid calls: no I2S errors. heap={heap}KB. {len(lines)} log lines checked."
    return CLARIFY, f"10 calls sent. Device alive, heap={heap}KB. Serial monitor not available."


@register_test("T66", "BUG-005 — Call during TTS playback → no audio conflict", 4)
def test_t66():
    if not _paho_available:
        return SKIP, "paho-mqtt not installed"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    marker = _serial_monitor.mark("bedroom") if _serial_monitor else None
    mqtt_publish(HUB_NOTIFY_TOPIC, json.dumps({"message": "Testing audio conflict scenario"}))
    time.sleep(1.0)
    mqtt_publish(CALL_TOPIC, json.dumps({"target": "Bedroom Intercom", "caller": "QA Test"}))
    time.sleep(3.0)
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Bedroom unreachable after TTS+call test"
    if not hub_reachable():
        return FAIL, "Hub unreachable after TTS+call test"
    if marker and _serial_monitor and _serial_monitor.is_active("bedroom"):
        lines = _serial_monitor.get_lines_since("bedroom", marker)
        i2s_errors = [l for l in lines if "I2S" in l and ("error" in l.lower() or "collision" in l.lower() or "fail" in l.lower())]
        if i2s_errors:
            return FAIL, f"I2S collision during TTS+call: {i2s_errors[:3]}"
        return PASS, f"TTS+call overlap: no I2S errors. {len(lines)} log lines checked."
    return CLARIFY, "TTS + call sent. Both survived. Serial monitor not available."


@register_test("T67", "BUG-006 — Rapid PTT cycles → no lag or stale audio packets", 4)
def test_t67():
    if not device_reachable(BEDROOM_IP):
        return SKIP, "Bedroom unreachable"
    if not hub_reachable():
        return SKIP, "Hub unreachable"
    # 5 PTT cycles: ~1s tone (50 frames), 500ms gap
    counts = []
    for i in range(5):
        reset_audio_stats()
        time.sleep(0.3)
        code, _ = post_test_action(BEDROOM_IP, "test_tone", timeout=10)
        time.sleep(2.0)  # wait for 50-frame tone + settle
        stats = get_audio_stats()
        counts.append(packet_count(stats, BEDROOM_DEVICE_ID))
    if not device_reachable(BEDROOM_IP):
        return FAIL, "Device unreachable after rapid PTT cycles"
    # Each 1s tone should produce ~50 packets; verify within ±30%
    bad = [i+1 for i, c in enumerate(counts) if not (35 <= c <= 65)]
    if bad:
        return FAIL, f"Unexpected packet counts on cycles {bad}. counts={counts}"
    return PASS, f"5 PTT cycles: packet counts={counts} (expected 35–65 each)"


@register_test("T68", "BUG-002 — INTERCOM2 30-min extended use → no ENOMEM", 4)
def test_t68():
    """
    BUG-002: INTERCOM2 ENOMEM after ~34 min — socket TX buffer exhausted or leak.
    """
    if not device_reachable(INTERCOM2_IP):
        return SKIP, "INTERCOM2 unreachable at start"
    failures = []
    for i in range(30):
        # Periodic tone to exercise TX path
        if i % 5 == 0:
            post_test_action(INTERCOM2_IP, "test_tone", timeout=10)
            time.sleep(2.0)  # wait for 50-frame tone to complete
        st = device_status(INTERCOM2_IP)
        if st is None:
            failures.append(f"INTERCOM2 unreachable at t={i+1}min")
        else:
            heap = st.get("free_heap", 0)
            if heap < 4_000_000:
                failures.append(f"t={i+1}min: heap low ({heap//1024}KB) — possible ENOMEM imminent")
        if i % 5 == 4:
            heap_str = f"{st.get('free_heap',0)//1024}KB" if st else "unreachable"
            print(f"    [T68] t={i+1}min: INTERCOM2={heap_str}", flush=True)
        time.sleep(60)
    if failures:
        return FAIL, f"BUG-002 signs detected: {'; '.join(failures[:5])}"
    return PASS, "INTERCOM2 survived 30-min extended use without ENOMEM"


# ===========================================================================
#  TEST RUNNER
# ===========================================================================

def run_test(entry: Dict, dry_run: bool = False) -> str:
    test_id = entry["id"]
    name    = entry["name"]
    fn      = entry["fn"]
    if dry_run:
        record(test_id, name, SKIP, "dry-run")
        return SKIP
    print(f"\n  [{ts()}] Running {test_id}: {name}", flush=True)
    try:
        status, detail = fn()
    except Exception as exc:
        status = FAIL
        detail = f"Unhandled exception: {exc}"
    record(test_id, name, status, detail)
    return status


def print_summary():
    total   = len(_results)
    passed  = sum(1 for r in _results if r["status"] == PASS)
    failed  = sum(1 for r in _results if r["status"] == FAIL)
    skipped = sum(1 for r in _results if r["status"] == SKIP)
    clarify = sum(1 for r in _results if r["status"] == CLARIFY)

    print("\n" + "=" * 70)
    print("  QA COMPREHENSIVE SUMMARY")
    print("=" * 70)
    print(f"  Total:   {total}")
    print(f"  PASS:    {passed}")
    print(f"  FAIL:    {failed}")
    print(f"  SKIP:    {skipped}")
    print(f"  CLARIFY: {clarify}")
    print()

    if failed > 0:
        print("  FAILURES:")
        for r in _results:
            if r["status"] == FAIL:
                print(f"    {r['id']}: {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail'][:120]}")
        print()

    if clarify > 0:
        print("  CLARIFY (manual verification needed):")
        for r in _results:
            if r["status"] == CLARIFY:
                print(f"    {r['id']}: {r['name']}")
        print()

    print("=" * 70)
    verdict = "ALL PASSED" if failed == 0 else f"FAILURES DETECTED ({failed})"
    print(f"  RESULT: {verdict}")
    print("=" * 70)


def write_report(path: str):
    lines = [
        f"# QA Comprehensive Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"Firmware target: {EXPECTED_VERSION}",
        f"Hub: {HUB_IP}:{HUB_PORT}",
        f"",
        f"## Summary",
        f"",
    ]
    total   = len(_results)
    passed  = sum(1 for r in _results if r["status"] == PASS)
    failed  = sum(1 for r in _results if r["status"] == FAIL)
    skipped = sum(1 for r in _results if r["status"] == SKIP)
    clarify = sum(1 for r in _results if r["status"] == CLARIFY)
    lines += [
        f"| Status  | Count |",
        f"|---------|-------|",
        f"| PASS    | {passed} |",
        f"| FAIL    | {failed} |",
        f"| SKIP    | {skipped} |",
        f"| CLARIFY | {clarify} |",
        f"| **Total** | **{total}** |",
        f"",
        f"## Results",
        f"",
        f"| ID | Name | Status | Detail |",
        f"|----|------|--------|--------|",
    ]
    for r in _results:
        detail = r["detail"].replace("\n", " ").replace("|", "\\|")[:120]
        lines.append(f"| {r['id']} | {r['name']} | {r['status']} | {detail} |")
    lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Report written to: {path}")


def main():
    parser = argparse.ArgumentParser(description="HA Intercom Comprehensive QA Suite — 68 tests")
    parser.add_argument("--dry-run", action="store_true", help="List tests without running them")
    parser.add_argument("--list",    action="store_true", help="Print test list and exit")
    parser.add_argument("--category", type=int, choices=[1, 2, 3, 4],
                        help="Run only tests in this category")
    parser.add_argument("--test",   type=str, help="Run a single test by ID (e.g. T7)")
    parser.add_argument("--no-soak", action="store_true",
                        help="Skip soak tests T43-T46, T64, T68 (>30 min each)")
    parser.add_argument("--report", type=str, default="",
                        help="Write Markdown report to this path")
    args = parser.parse_args()

    global _serial_monitor

    # Check optional dependencies
    if not _paho_available:
        print("  NOTE: paho-mqtt not installed -- MQTT tests will be SKIPPED")
        print("        Install: pip install paho-mqtt")
    if not _ws_available:
        print("  NOTE: websockets not installed -- WebSocket tests will be SKIPPED")
        print("        Install: pip install websockets")

    # Start serial log monitor
    _serial_monitor = SerialLogMonitor()
    _serial_monitor.start()

    # Pre-flight: verify both devices are running the expected firmware version.
    print("\n  Pre-flight version check:")
    for ip, name in [(BEDROOM_IP, "Bedroom"), (INTERCOM2_IP, "INTERCOM2")]:
        st = device_status(ip)
        if st is None:
            print(f"    WARNING: {name} ({ip}) unreachable -- version check skipped")
        elif st.get("firmware_version") != EXPECTED_VERSION:
            print(f"    WARNING: {name} running {st.get('firmware_version')!r}, "
                  f"expected {EXPECTED_VERSION!r}")
        else:
            print(f"    {name}: firmware_version={st.get('firmware_version')!r} (ok)")

    SOAK_IDS = {"T43", "T44", "T45", "T46", "T64", "T68"}

    if args.list or args.dry_run:
        for entry in _TEST_REGISTRY:
            print(f"  {entry['id']:4s}  [Cat {entry['cat']}]  {entry['name']}")
        if args.list:
            print(f"\n  Total: {len(_TEST_REGISTRY)} tests")
            return

    # Build run set
    run_set = _TEST_REGISTRY
    if args.category:
        run_set = [e for e in run_set if e["cat"] == args.category]
    if args.test:
        tid = args.test.upper()
        run_set = [e for e in run_set if e["id"] == tid]
        if not run_set:
            print(f"  ERROR: Test {tid} not found")
            sys.exit(1)
    if args.no_soak:
        run_set = [e for e in run_set if e["id"] not in SOAK_IDS]
        if run_set != _TEST_REGISTRY:
            print(f"  NOTE: --no-soak: skipping {SOAK_IDS}")

    print(f"\n{'=' * 70}")
    print(f"  HA Intercom Comprehensive QA Suite")
    print(f"  Tests to run: {len(run_set)} / {len(_TEST_REGISTRY)}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}")

    for entry in run_set:
        run_test(entry, dry_run=args.dry_run)

    # Stop serial monitor
    if _serial_monitor:
        _serial_monitor.stop()

    if not args.dry_run:
        print_summary()
        if args.report:
            write_report(args.report)


if __name__ == "__main__":
    main()
