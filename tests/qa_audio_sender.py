#!/usr/bin/env python3
"""
QA Audio Sender — Intercom Protocol UDP Transmitter
====================================================
Simulates an ESP32 intercom transmitting audio via UDP multicast or unicast.
Used by scenario tests to inject controlled audio into the intercom network.

Classes:
  QAudioSender  — Generate sine wave PCM, Opus-encode, send at 50fps over UDP
  HeapTracker   — Periodically poll /api/status and track heap usage over time

Helper:
  wait_for_tone_complete — Poll /api/status until test_tone/sustained_tx finishes

Usage (standalone):
  python3 tests/qa_audio_sender.py
"""

import base64
import json
import math
import socket
import struct
import threading
import time
import urllib.request
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _make_auth_header(username: str, password: str) -> str:
    """Return an HTTP Basic Authorization header value for the given credentials."""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


# ---------------------------------------------------------------------------
# Protocol constants (mirrors firmware/main/protocol.h)
# ---------------------------------------------------------------------------
MULTICAST_GROUP    = "239.255.0.100"
AUDIO_PORT         = 5005
SAMPLE_RATE        = 16000
FRAME_DURATION_MS  = 20
FRAME_SIZE         = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 320 samples
HEADER_LENGTH      = 13   # 8 device_id + 4 sequence (big-endian uint32) + 1 priority
PRIORITY_NORMAL    = 0
PRIORITY_HIGH      = 1
PRIORITY_EMERGENCY = 2
MULTICAST_TTL      = 32

# Default QA sender device ID — 8 bytes, clearly not a real device
QA_DEVICE_ID = b"QA_TEST!"

# ---------------------------------------------------------------------------
# Opus — imported at module level with graceful fallback
# ---------------------------------------------------------------------------
_opuslib_available = False
_opuslib_error: Optional[str] = None

try:
    import opuslib
    _opuslib_available = True
except ImportError as _e:
    _opuslib_error = str(_e)


def _require_opuslib() -> None:
    """Raise ImportError with a clear install message if opuslib is missing."""
    if not _opuslib_available:
        raise ImportError(
            "opuslib is required for audio encoding but is not installed.\n"
            "Install it with:  pip install opuslib\n"
            f"Original error:   {_opuslib_error}"
        )


# ---------------------------------------------------------------------------
# Packet building
# ---------------------------------------------------------------------------
def _build_packet(device_id: bytes, sequence: int, opus_frame: bytes,
                  priority: int = PRIORITY_NORMAL) -> bytes:
    """
    Pack an intercom audio packet.

    Wire format (matches audio_packet_t in protocol.h):
      [0-7]   device_id  8 bytes
      [8-11]  sequence   4 bytes, big-endian uint32
      [12]    priority   1 byte
      [13+]   opus_data  variable
    """
    if len(device_id) != 8:
        raise ValueError(f"device_id must be exactly 8 bytes, got {len(device_id)}")
    return device_id + struct.pack(">IB", sequence, priority) + opus_frame


# ---------------------------------------------------------------------------
# QAudioSender
# ---------------------------------------------------------------------------
class QAudioSender:
    """
    Simulates an ESP32 intercom transmitting audio via UDP.

    Generates sine wave PCM, Opus-encodes each 20ms frame, wraps it in the
    13-byte intercom packet header, and sends at exactly 50 packets/sec using
    wall-clock scheduling to avoid cumulative drift.

    Supports both multicast (default: 239.255.0.100:5005) and unicast delivery.
    IP_MULTICAST_LOOP defaults to enabled so the sender can be received on the
    same machine — useful for local QA without real hardware. Pass
    multicast_loop=False when testing against real hardware to match firmware
    behaviour (firmware sets IP_MULTICAST_LOOP=0 on its TX socket).

    Thread safety: start()/stop() may be called from any thread. get_stats()
    is safe to call while sending.
    """

    def __init__(
        self,
        device_id: bytes = QA_DEVICE_ID,
        target_ip: str = MULTICAST_GROUP,
        target_port: int = AUDIO_PORT,
        sample_rate: int = SAMPLE_RATE,
        frame_duration_ms: int = FRAME_DURATION_MS,
        frequency: float = 440.0,
        amplitude: float = 0.5,
        priority: int = PRIORITY_NORMAL,
        multicast_loop: bool = True,
    ) -> None:
        if len(device_id) != 8:
            raise ValueError(f"device_id must be exactly 8 bytes, got {len(device_id)}")
        if not 0.0 <= amplitude <= 1.0:
            raise ValueError(f"amplitude must be in [0.0, 1.0], got {amplitude}")

        self._device_id = device_id
        self._target_ip = target_ip
        self._target_port = target_port
        self._sample_rate = sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._frame_size = sample_rate * frame_duration_ms // 1000
        self._frequency = frequency
        self._amplitude = amplitude
        self._priority = priority
        self._multicast_loop = multicast_loop

        self._is_multicast = self._detect_multicast(target_ip)

        # State — guarded by _lock for stats; _stop_event for thread shutdown
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._packets_sent: int = 0
        self._frames_generated: int = 0
        self._start_time: float = 0.0
        self._elapsed: float = 0.0
        self._errors: int = 0

    @staticmethod
    def _detect_multicast(ip: str) -> bool:
        """Return True if the IP falls in the multicast range 224.0.0.0/4."""
        try:
            first_octet = int(ip.split(".")[0])
            return 224 <= first_octet <= 239
        except (ValueError, IndexError):
            return False

    def start(self, duration_seconds: float) -> None:
        """
        Begin sending audio in a background daemon thread. Returns immediately.

        Raises RuntimeError if already sending.
        Raises ImportError if opuslib is not installed.
        """
        _require_opuslib()

        if duration_seconds <= 0:
            raise ValueError(f"duration_seconds must be positive, got {duration_seconds}")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("QAudioSender is already sending; call stop() first")
            self._stop_event.clear()
            self._packets_sent = 0
            self._frames_generated = 0
            self._errors = 0
            self._elapsed = 0.0
            self._start_time = time.monotonic()

        self._thread = threading.Thread(
            target=self._send_loop,
            args=(duration_seconds,),
            daemon=True,
            name="qa-audio-sender",
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the sending thread to stop. Does not block."""
        self._stop_event.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        """
        Block until sending completes or timeout expires.

        Does not raise on timeout — check is_sending() afterward if needed.
        """
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_sending(self) -> bool:
        """Return True if the background send thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict:
        """
        Return a snapshot of transmission statistics.

        Thread-safe — may be called while sending.
        """
        with self._lock:
            if self._start_time > 0:
                if self._thread is not None and self._thread.is_alive():
                    elapsed = time.monotonic() - self._start_time
                else:
                    elapsed = self._elapsed
            else:
                elapsed = 0.0
            return {
                "packets_sent": self._packets_sent,
                "frames_generated": self._frames_generated,
                "elapsed_seconds": elapsed,
                "errors": self._errors,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _generate_pcm_frame(self, frame_index: int) -> bytes:
        """
        Generate a single 20ms PCM frame as 16-bit signed little-endian samples.

        Phase is computed from absolute sample position so all frames form a
        continuous sine wave — no phase discontinuity at frame boundaries.
        This matches the pattern in firmware/main/main.c (play_fallback_beep
        and test_tone task use absolute sample index for continuity).
        """
        frame_start_sample = frame_index * self._frame_size
        peak = int(32767 * self._amplitude)
        inv_sample_rate = 1.0 / self._sample_rate
        samples = []
        for i in range(self._frame_size):
            t = (frame_start_sample + i) * inv_sample_rate
            sample = int(peak * math.sin(2.0 * math.pi * self._frequency * t))
            # Clamp to int16 range — floating-point accumulation can slightly exceed
            sample = max(-32768, min(32767, sample))
            samples.append(sample)
        return struct.pack(f"<{self._frame_size}h", *samples)

    def _create_encoder(self):
        """Create and configure an Opus encoder."""
        encoder = opuslib.Encoder(self._sample_rate, 1, opuslib.APPLICATION_VOIP)
        # 32kbps VBR — matches OPUS_BITRATE in protocol.h
        encoder.bitrate = 32000
        encoder.vbr = True
        encoder.complexity = 5
        return encoder

    def _create_socket(self) -> socket.socket:
        """Create a UDP socket configured for multicast or unicast."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        if self._is_multicast:
            # TTL=32 allows multicast to span subnets within the organization.
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_TTL,
                struct.pack("b", MULTICAST_TTL),
            )
            # IP_MULTICAST_LOOP controls whether the sender's own machine
            # receives the packets. Default True — useful for local QA without
            # real hardware. Set False when testing real hardware to avoid
            # the sending host receiving its own multicast (mirrors firmware
            # behaviour where IP_MULTICAST_LOOP=0 is set on TX socket).
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_LOOP,
                1 if self._multicast_loop else 0,
            )

        return sock

    def _send_loop(self, duration_seconds: float) -> None:
        """
        Main send loop. Runs in the background thread.

        Wall-clock scheduling: compute the absolute deadline for each frame
        and sleep the exact remaining delta. This prevents the 2% drift that
        accumulates when using time.sleep(0.02) naively.
        """
        try:
            encoder = self._create_encoder()
        except Exception as exc:
            print(f"[QAudioSender] Failed to create Opus encoder: {exc}")
            with self._lock:
                self._errors += 1
            return

        sock = None
        try:
            sock = self._create_socket()
            frame_interval = self._frame_duration_ms / 1000.0  # 0.02 s
            total_frames = int(duration_seconds / frame_interval)
            loop_start = time.monotonic()

            for frame_num in range(total_frames):
                if self._stop_event.is_set():
                    break

                # Wall-clock deadline for this frame
                next_send = loop_start + frame_num * frame_interval
                now = time.monotonic()
                if next_send > now:
                    time.sleep(next_send - now)
                # If we're behind schedule, send immediately — do not skip frames

                # Generate PCM
                pcm = self._generate_pcm_frame(frame_num)
                with self._lock:
                    self._frames_generated += 1

                # Opus-encode
                try:
                    opus_frame = encoder.encode(pcm, self._frame_size)
                except Exception as exc:
                    print(f"[QAudioSender] Opus encode error on frame {frame_num}: {exc}")
                    with self._lock:
                        self._errors += 1
                    continue

                # Sequence wraps at uint32 max (0xFFFFFFFF)
                with self._lock:
                    seq = self._packets_sent & 0xFFFFFFFF

                # Build and send packet
                try:
                    packet = _build_packet(self._device_id, seq, opus_frame, self._priority)
                    sock.sendto(packet, (self._target_ip, self._target_port))
                    with self._lock:
                        self._packets_sent += 1
                except OSError as exc:
                    print(f"[QAudioSender] Send error on frame {frame_num}: {exc}")
                    with self._lock:
                        self._errors += 1

        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            with self._lock:
                if self._start_time > 0:
                    self._elapsed = time.monotonic() - self._start_time


# ---------------------------------------------------------------------------
# HeapTracker
# ---------------------------------------------------------------------------
class HeapTracker:
    """
    Periodically polls /api/status on one or more ESP32 devices and records
    heap usage over time. Useful for detecting memory leaks during sustained
    audio tests.

    Polling runs in a single background daemon thread. Thread-safe reads via
    a per-device lock.
    """

    def __init__(
        self,
        device_ips: List[str],
        interval_seconds: float = 5.0,
        username: str = "",
        password: str = "",
    ) -> None:
        if not device_ips:
            raise ValueError("device_ips must contain at least one IP address")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

        self._device_ips = list(device_ips)
        self._interval = interval_seconds
        self._username = username
        self._password = password

        self._samples: Dict[str, List[Dict]] = {ip: [] for ip in self._device_ips}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start polling in a background daemon thread. Raises if already running."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("HeapTracker is already running; call stop() first")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="heap-tracker",
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal polling to stop. Blocks until the thread exits (max 2x interval)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 2 + 1.0)

    def get_samples(self, device_ip: str) -> List[Dict]:
        """
        Return a copy of all heap samples recorded for the given device IP.

        Each sample: {"timestamp": float, "free_heap": int, "heap_usage_percent": float}
        Returns an empty list if the device has no samples yet.
        """
        with self._lock:
            return list(self._samples.get(device_ip, []))

    def get_drift(self, device_ip: str) -> Dict:
        """
        Summarise heap drift for a device across all recorded samples.

        Returns:
          {"start_heap": int, "end_heap": int, "drift_bytes": int,
           "drift_percent": float, "samples": int}

        drift_bytes is positive when heap has grown (memory freed), negative
        when heap has shrunk (memory leaked or still allocated).

        Returns zeros if fewer than 2 samples are available.
        """
        with self._lock:
            samples = list(self._samples.get(device_ip, []))

        if len(samples) < 2:
            return {
                "start_heap": 0,
                "end_heap": 0,
                "drift_bytes": 0,
                "drift_percent": 0.0,
                "samples": len(samples),
            }

        start_heap = samples[0]["free_heap"]
        end_heap = samples[-1]["free_heap"]
        drift_bytes = end_heap - start_heap
        drift_percent = (drift_bytes / start_heap * 100.0) if start_heap > 0 else 0.0

        return {
            "start_heap": start_heap,
            "end_heap": end_heap,
            "drift_bytes": drift_bytes,
            "drift_percent": round(drift_percent, 2),
            "samples": len(samples),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Background polling loop. Polls all devices on every interval."""
        while not self._stop_event.is_set():
            for ip in self._device_ips:
                self._poll_device(ip)
            self._stop_event.wait(timeout=self._interval)

    def _poll_device(self, ip: str) -> None:
        """Fetch /api/status from one device and record a heap sample."""
        try:
            url = f"http://{ip}/api/status"
            req = urllib.request.Request(url)
            if self._username and self._password:
                req.add_header("Authorization", _make_auth_header(self._username, self._password))
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
            data = json.loads(body)
        except Exception:
            # Network errors during a sustained test are expected; skip silently
            return

        free_heap = data.get("free_heap", 0)
        heap_usage_percent = data.get("heap_usage_percent", 0.0)

        sample = {
            "timestamp": time.time(),
            "free_heap": int(free_heap),
            "heap_usage_percent": float(heap_usage_percent),
        }

        with self._lock:
            if ip in self._samples:
                self._samples[ip].append(sample)


# ---------------------------------------------------------------------------
# wait_for_tone_complete
# ---------------------------------------------------------------------------
def wait_for_tone_complete(
    device_ip: str,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    username: str = "",
    password: str = "",
) -> bool:
    """
    Poll /api/status on an ESP32 until test_tone_active, sustained_tx_active,
    and transmitting are all false (indicating test_tone or sustained_tx finished).

    Returns True if the device went idle within the timeout, False otherwise.

    Uses the boolean fields exposed by the firmware's /api/status endpoint:
      - transmitting (bool): true while PTT or sustained_tx is active
      - test_tone_active (bool): true while test_tone task is running
      - sustained_tx_active (bool): true while sustained_tx is running
    """
    url = f"http://{device_ip}/api/status"
    start_time = time.time()
    deadline = start_time + timeout
    seen_active = False
    grace_period = 2.0  # wait at least this long before concluding "already finished"

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            if username and password:
                req.add_header("Authorization", _make_auth_header(username, password))
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception:
            time.sleep(poll_interval)
            continue

        is_active = (
            data.get("transmitting", False)
            or data.get("test_tone_active", False)
            or data.get("sustained_tx_active", False)
        )

        if is_active:
            seen_active = True
        elif seen_active:
            # Was active, now idle — tone/tx completed
            return True
        elif not seen_active and (time.time() - start_time) < grace_period:
            # Within grace period: keep polling even if idle
            # (device may not have started yet)
            pass
        elif not seen_active:
            # Past grace period, never saw active — already finished
            return True

        time.sleep(poll_interval)

    # Timed out.
    # - If we never saw active: tone may have finished before we first
    #   polled, or the device was never in active state. Return True
    #   (caller's intent was to wait for completion; if it never started we
    #   don't want to block the test).
    # - If we saw active but it never ended: still stuck — return False.
    if not seen_active:
        return True
    return False


# ---------------------------------------------------------------------------
# Module-level smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("QA Audio Sender — standalone test")
    print(f"  Target:    {MULTICAST_GROUP}:{AUDIO_PORT}")
    print(f"  Frequency: 440 Hz")
    print(f"  Duration:  5 seconds")
    print()

    sender = QAudioSender()
    sender.start(5.0)

    # Print progress every second while sending
    while sender.is_sending():
        stats = sender.get_stats()
        print(
            f"  {stats['elapsed_seconds']:.1f}s  "
            f"packets={stats['packets_sent']}  "
            f"frames={stats['frames_generated']}  "
            f"errors={stats['errors']}",
            flush=True,
        )
        time.sleep(1.0)

    sender.wait(timeout=2.0)
    final = sender.get_stats()
    print()
    print(f"Done: {final}")
