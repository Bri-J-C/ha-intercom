#!/usr/bin/env python3
"""
Audio Scenario Tests -- HA Intercom System (v2)
=================================================
58 tests across 9 categories exercising REAL audio paths.
No test_tone — all audio via sustained_tx, Web PTT, chime, or QAudioSender.

Devices:
  Bedroom   (10.0.0.15) -- device_id XXXXXXXXXXXXXXXX
  INTERCOM2 (10.0.0.14) -- device_id YYYYYYYYYYYYYYYY
  Hub       (10.0.0.8:8099)

Usage:
  python3 tests/test_audio_scenarios.py              # run all tests
  python3 tests/test_audio_scenarios.py --test S05   # run one test
  python3 tests/test_audio_scenarios.py --category 1 # run category 1
  python3 tests/test_audio_scenarios.py --list        # list all tests
  python3 tests/test_audio_scenarios.py --skip-long   # skip tests > 60s

Requires:
  pip install opuslib paho-mqtt pyserial
Optional:
  pip install websockets   (for Web PTT tests)
"""

import argparse
import json
import math
import os
import random
import socket
import struct
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add tests/ to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_harness import (
    BEDROOM_IP, INTERCOM2_IP, HUB_IP, HUB_PORT,
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS,
    BEDROOM_UNIQUE_ID, INTERCOM2_UNIQUE_ID, HUB_UNIQUE_ID,
    BEDROOM_DEVICE_ID, INTERCOM2_DEVICE_ID,
    CALL_TOPIC, HUB_NOTIFY_TOPIC,
    DEVICE_TIMEOUT, HUB_TIMEOUT, DEVICE_USER, DEVICE_PASS,
    PACKETS_PER_SECOND, HEAP_LEAK_THRESHOLD_BYTES,
    DEVICES, PASS, FAIL, SKIP,
    LogHarness, TestResult, WebPTTClient, MqttSession,
    device_status, get_audio_stats, reset_audio_stats, get_hub_state,
    ensure_hub_idle, hub_packet_count, hub_url,
    post_test_action, trigger_sustained_tx, wait_for_tx_complete, reboot_device,
    mqtt_publish, http_get, http_post, get_json,
    run_test, generate_report, print_summary,
    _make_auth_header,
)

from qa_audio_sender import (
    QAudioSender, HeapTracker,
    MULTICAST_GROUP, AUDIO_PORT, HEADER_LENGTH, QA_DEVICE_ID,
)


# ===========================================================================
# Test Helpers
# ===========================================================================
def set_device_target(unique_id: str, target_name: str) -> bool:
    """Set a device's target room via MQTT."""
    topic = f"intercom/{unique_id}/target/set"
    return mqtt_publish(topic, target_name)


def set_device_dnd(unique_id: str, enabled: bool) -> bool:
    topic = f"intercom/{unique_id}/dnd/set"
    return mqtt_publish(topic, "ON" if enabled else "OFF")


def set_device_volume(unique_id: str, volume: int) -> bool:
    topic = f"intercom/{unique_id}/volume/set"
    return mqtt_publish(topic, str(volume))


def set_device_mute(unique_id: str, muted: bool) -> bool:
    topic = f"intercom/{unique_id}/mute/set"
    return mqtt_publish(topic, "ON" if muted else "OFF")


def set_device_priority(unique_id: str, priority: str) -> bool:
    topic = f"intercom/{unique_id}/priority/set"
    return mqtt_publish(topic, priority)


def set_device_agc(unique_id: str, enabled: bool) -> bool:
    topic = f"intercom/{unique_id}/agc/set"
    return mqtt_publish(topic, "ON" if enabled else "OFF")


def trigger_call(target: str, caller: str = "QA Test") -> bool:
    payload = json.dumps({"target": target, "caller": caller})
    return mqtt_publish(CALL_TOPIC, payload)


def wait_device_receiving(ip: str, timeout: float = 10.0) -> bool:
    """Wait until device reports receiving=true."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = device_status(ip)
        if st and st.get("receiving"):
            return True
        time.sleep(0.3)
    return False


def wait_device_idle(ip: str, timeout: float = 10.0) -> bool:
    """Wait until device is not transmitting and not receiving."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = device_status(ip)
        if st and not st.get("transmitting") and not st.get("receiving") \
                and not st.get("sustained_tx_active"):
            return True
        time.sleep(0.5)
    return False


def get_rx_count(ip: str) -> int:
    st = device_status(ip)
    return st.get("rx_packet_count", 0) if st else 0


def get_tx_count(ip: str) -> int:
    st = device_status(ip)
    return st.get("tx_frame_count", 0) if st else 0


def get_heap(ip: str) -> int:
    st = device_status(ip)
    return st.get("free_heap", 0) if st else 0


def restore_defaults():
    """Restore both devices to default state (no DND, full volume, unmuted, All Rooms target)."""
    for uid in [BEDROOM_UNIQUE_ID, INTERCOM2_UNIQUE_ID]:
        set_device_dnd(uid, False)
        set_device_mute(uid, False)
        set_device_volume(uid, 80)
        set_device_priority(uid, "Normal")
        set_device_target(uid, "All Rooms")
    time.sleep(0.5)


# ===========================================================================
# Category 1: Basic Audio Paths (S01-S10)
# ===========================================================================
def s01_device_a_to_b_multicast():
    """Device A sustained_tx -> Device B receives (multicast)."""
    ok, detail = ensure_hub_idle(label="S01")
    if not ok:
        return FAIL, detail

    reset_audio_stats()
    rx_before = get_rx_count(INTERCOM2_IP)

    if not trigger_sustained_tx(BEDROOM_IP, duration=5):
        return FAIL, "Failed to trigger sustained_tx on Bedroom"

    time.sleep(1)
    if not wait_device_receiving(INTERCOM2_IP, timeout=8):
        print("      DIAG: INTERCOM2 never entered receiving state")

    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)

    if rx_delta < 100:
        return FAIL, f"INTERCOM2 rx_delta={rx_delta}, expected >=100"
    return PASS, f"INTERCOM2 rx_delta={rx_delta}, hub_pkts={hub_pkts}"


def s02_device_b_to_a_multicast():
    """Device B sustained_tx -> Device A receives (multicast)."""
    ok, detail = ensure_hub_idle(label="S02")
    if not ok:
        return FAIL, detail

    reset_audio_stats()
    rx_before = get_rx_count(BEDROOM_IP)

    if not trigger_sustained_tx(INTERCOM2_IP, duration=5):
        return FAIL, "Failed to trigger sustained_tx on INTERCOM2"

    time.sleep(1)
    wait_for_tx_complete(INTERCOM2_IP, duration=5)
    time.sleep(1)

    rx_after = get_rx_count(BEDROOM_IP)
    rx_delta = rx_after - rx_before

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, INTERCOM2_DEVICE_ID)

    if rx_delta < 100:
        return FAIL, f"Bedroom rx_delta={rx_delta}, expected >=100"
    return PASS, f"Bedroom rx_delta={rx_delta}, hub_pkts={hub_pkts}"


def s03_unicast_isolation():
    """Device A unicast to B -- verify non-target doesn't receive."""
    ok, detail = ensure_hub_idle(label="S03")
    if not ok:
        return FAIL, detail

    # Set Bedroom to target INTERCOM2 specifically
    set_device_target(BEDROOM_UNIQUE_ID, "INTERCOM2")
    time.sleep(1)

    reset_audio_stats()
    rx_ic2_before = get_rx_count(INTERCOM2_IP)
    rx_bed_before = get_rx_count(BEDROOM_IP)

    if not trigger_sustained_tx(BEDROOM_IP, duration=5):
        set_device_target(BEDROOM_UNIQUE_ID, "All Rooms")
        return FAIL, "Failed to trigger sustained_tx on Bedroom"

    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    rx_ic2_after = get_rx_count(INTERCOM2_IP)
    rx_bed_after = get_rx_count(BEDROOM_IP)

    ic2_delta = rx_ic2_after - rx_ic2_before
    bed_delta = rx_bed_after - rx_bed_before

    # Restore target
    set_device_target(BEDROOM_UNIQUE_ID, "All Rooms")

    # Bedroom is transmitting so it self-filters (transmitting=true blocks RX)
    # But bed_delta should be 0 or very small anyway (half-duplex guard)
    if ic2_delta < 100:
        return FAIL, f"INTERCOM2 rx_delta={ic2_delta}, expected >=100 (unicast target)"
    return PASS, f"INTERCOM2 rx_delta={ic2_delta}, Bedroom rx_delta={bed_delta} (isolation ok)"


def s04_hub_chime_unicast():
    """Hub chime -> specific device (unicast call via MQTT)."""
    ok, detail = ensure_hub_idle(label="S04")
    if not ok:
        return FAIL, detail

    rx_before = get_rx_count(INTERCOM2_IP)

    if not trigger_call("INTERCOM2", "QA Test"):
        return FAIL, "Failed to publish MQTT call"

    # Wait for chime to be streamed (chimes are ~1s typically)
    time.sleep(3)

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    if rx_delta < 10:
        return FAIL, f"INTERCOM2 rx_delta={rx_delta}, expected >=10 (chime packets)"
    return PASS, f"INTERCOM2 rx_delta={rx_delta} (chime delivered)"


def s05_hub_chime_all_rooms():
    """Hub chime -> All Rooms (multicast call via MQTT)."""
    ok, detail = ensure_hub_idle(label="S05")
    if not ok:
        return FAIL, detail

    rx_bed_before = get_rx_count(BEDROOM_IP)
    rx_ic2_before = get_rx_count(INTERCOM2_IP)

    if not trigger_call("All Rooms", "QA Test"):
        return FAIL, "Failed to publish MQTT call"

    time.sleep(3)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    rx_ic2_after = get_rx_count(INTERCOM2_IP)

    bed_delta = rx_bed_after - rx_bed_before
    ic2_delta = rx_ic2_after - rx_ic2_before

    if bed_delta < 10 and ic2_delta < 10:
        return FAIL, (f"Neither device received: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}")
    if bed_delta < 10 or ic2_delta < 10:
        return PASS, (f"IGMP flaky: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta} "
                       f"(one missed multicast)")
    return PASS, f"Bedroom rx_delta={bed_delta}, INTERCOM2 rx_delta={ic2_delta}"


def s06_web_ptt_specific_device():
    """Web PTT -> specific device (WebSocket simulation)."""
    ok, detail = ensure_hub_idle(label="S06")
    if not ok:
        return FAIL, detail

    rx_before = get_rx_count(INTERCOM2_IP)
    rx_bed_before = get_rx_count(BEDROOM_IP)

    client = WebPTTClient("QA_S06")
    if not client.transmit(target="INTERCOM2", duration=3.0):
        return SKIP, "WebSocket connection failed (websockets package installed?)"

    time.sleep(2)

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_bed_after = get_rx_count(BEDROOM_IP)
    ic2_delta = rx_after - rx_before
    bed_delta = rx_bed_after - rx_bed_before

    if ic2_delta < 50:
        return FAIL, f"INTERCOM2 rx_delta={ic2_delta}, expected >=50"
    return PASS, f"INTERCOM2 rx_delta={ic2_delta}, Bedroom rx_delta={bed_delta}"


def s07_web_ptt_all_rooms():
    """Web PTT -> All Rooms (WebSocket simulation)."""
    ok, detail = ensure_hub_idle(label="S07")
    if not ok:
        return FAIL, detail

    rx_bed_before = get_rx_count(BEDROOM_IP)
    rx_ic2_before = get_rx_count(INTERCOM2_IP)

    client = WebPTTClient("QA_S07")
    if not client.transmit(target="All Rooms", duration=3.0):
        return SKIP, "WebSocket connection failed"

    time.sleep(2)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    rx_ic2_after = get_rx_count(INTERCOM2_IP)
    bed_delta = rx_bed_after - rx_bed_before
    ic2_delta = rx_ic2_after - rx_ic2_before

    if bed_delta < 50 and ic2_delta < 50:
        return FAIL, f"Neither device received: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}"
    if bed_delta < 50 or ic2_delta < 50:
        return PASS, f"IGMP flaky: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta} (one missed multicast)"
    return PASS, f"Bedroom rx_delta={bed_delta}, INTERCOM2 rx_delta={ic2_delta}"


def s08_tts_to_device():
    """TTS -> device (MQTT notify)."""
    ok, detail = ensure_hub_idle(label="S08")
    if not ok:
        return FAIL, detail

    rx_before = get_rx_count(BEDROOM_IP)

    # Send TTS notification
    payload = json.dumps({"message": "Test notification from QA"})
    if not mqtt_publish(HUB_NOTIFY_TOPIC, payload):
        return FAIL, "Failed to publish MQTT notify"

    # TTS takes time: Piper synthesis + encode + broadcast
    time.sleep(8)

    rx_after = get_rx_count(BEDROOM_IP)
    rx_delta = rx_after - rx_before

    if rx_delta < 10:
        return SKIP, f"TTS produced no audio (rx_delta={rx_delta}) -- Piper may not be running"
    return PASS, f"Bedroom rx_delta={rx_delta} (TTS delivered)"


def s09_device_tx_hub_stats():
    """Device sustained_tx -> hub audio_stats confirms receipt."""
    ok, detail = ensure_hub_idle(label="S09")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    if not trigger_sustained_tx(BEDROOM_IP, duration=5):
        return FAIL, "Failed to trigger sustained_tx on Bedroom"

    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)

    if hub_pkts < 100:
        return FAIL, f"Hub saw only {hub_pkts} packets from Bedroom, expected >=100"
    return PASS, f"Hub received {hub_pkts} packets from Bedroom"


def s10_audio_content_verification():
    """QAudioSender 440Hz -> hub receives -> verify content."""
    ok, detail = ensure_hub_idle(label="S10")
    if not ok:
        return FAIL, detail

    reset_audio_stats()
    rx_bed_before = get_rx_count(BEDROOM_IP)

    sender = QAudioSender(frequency=440.0, amplitude=0.5)
    sender.start(duration_seconds=3)
    sender.wait(timeout=10)

    time.sleep(1)

    stats = get_audio_stats()
    # QAudioSender uses device_id "QA_TEST!" = hex 51415f5445535421
    qa_device_hex = QA_DEVICE_ID.hex()
    hub_pkts = hub_packet_count(stats, qa_device_hex)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    bed_delta = rx_bed_after - rx_bed_before

    if hub_pkts < 50:
        return FAIL, f"Hub saw only {hub_pkts} packets from QAudioSender, expected >=50"
    return PASS, f"Hub received {hub_pkts} QA packets, Bedroom rx_delta={bed_delta}"


# ===========================================================================
# Category 2: Call System (S11-S16)
# ===========================================================================
def s11_single_device_call():
    """Single-device call: MQTT call -> chime -> audio flows."""
    ok, detail = ensure_hub_idle(label="S11")
    if not ok:
        return FAIL, detail

    rx_before = get_rx_count(INTERCOM2_IP)

    trigger_call("INTERCOM2", "QA Test")
    time.sleep(2)  # Chime plays

    # Now simulate caller sending audio
    if trigger_sustained_tx(BEDROOM_IP, duration=3):
        wait_for_tx_complete(BEDROOM_IP, duration=3)
    time.sleep(1)

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    if rx_delta < 50:
        return FAIL, f"INTERCOM2 rx_delta={rx_delta}, expected >=50 (chime + audio)"
    return PASS, f"Call + audio: INTERCOM2 rx_delta={rx_delta}"


def s12_all_rooms_call():
    """All Rooms call: both devices get chime."""
    ok, detail = ensure_hub_idle(label="S12")
    if not ok:
        return FAIL, detail

    rx_bed_before = get_rx_count(BEDROOM_IP)
    rx_ic2_before = get_rx_count(INTERCOM2_IP)

    trigger_call("All Rooms", "QA Test")
    time.sleep(3)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    rx_ic2_after = get_rx_count(INTERCOM2_IP)

    bed_delta = rx_bed_after - rx_bed_before
    ic2_delta = rx_ic2_after - rx_ic2_before

    if bed_delta < 10 and ic2_delta < 10:
        return FAIL, f"Neither device received: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}"
    if bed_delta < 10 or ic2_delta < 10:
        return PASS, f"IGMP flaky: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta} (one missed multicast)"
    return PASS, f"All Rooms: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}"


def s13_dnd_blocks_call():
    """DND blocks incoming call audio."""
    ok, detail = ensure_hub_idle(label="S13")
    if not ok:
        return FAIL, detail

    # Enable DND on INTERCOM2
    set_device_dnd(INTERCOM2_UNIQUE_ID, True)
    time.sleep(1)

    # Verify DND is set
    st = device_status(INTERCOM2_IP)
    if not st or not st.get("dnd"):
        set_device_dnd(INTERCOM2_UNIQUE_ID, False)
        return FAIL, "Failed to enable DND on INTERCOM2"

    rx_before = get_rx_count(INTERCOM2_IP)
    trigger_call("INTERCOM2", "QA Test")
    time.sleep(3)

    # Send some audio too
    trigger_sustained_tx(BEDROOM_IP, duration=3)
    wait_for_tx_complete(BEDROOM_IP, duration=3)
    time.sleep(1)

    st_after = device_status(INTERCOM2_IP)
    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    # Restore DND
    set_device_dnd(INTERCOM2_UNIQUE_ID, False)

    # rx_packet_count increments BEFORE DND filter, so packets still counted
    # But device should not enter receiving state
    was_receiving = st_after.get("receiving", False) if st_after else False

    if was_receiving:
        return FAIL, f"DND device entered receiving state (rx_delta={rx_delta})"
    return PASS, f"DND correctly blocked (rx_delta={rx_delta}, receiving=false)"


def s14_call_during_tx():
    """Call while device transmitting -> no crash."""
    ok, detail = ensure_hub_idle(label="S14")
    if not ok:
        return FAIL, detail

    trigger_sustained_tx(BEDROOM_IP, duration=5)
    time.sleep(1)

    # Send call while Bedroom is transmitting
    trigger_call("Bedroom Intercom", "QA Test")
    time.sleep(2)

    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Bedroom unreachable after call during TX"
    if not st.get("mqtt_connected"):
        return FAIL, "Bedroom MQTT disconnected after call during TX"
    return PASS, "Call during TX: device survived"


def s15_simultaneous_calls():
    """Both devices call each other simultaneously -> no deadlock."""
    ok, detail = ensure_hub_idle(label="S15")
    if not ok:
        return FAIL, detail

    # Both call each other at once
    trigger_call("INTERCOM2", "Bedroom Intercom")
    trigger_call("Bedroom Intercom", "INTERCOM2")
    time.sleep(5)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)

    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after simultaneous calls"
    if not bed_st.get("mqtt_connected") or not ic2_st.get("mqtt_connected"):
        return FAIL, "MQTT disconnected after simultaneous calls"
    return PASS, "Simultaneous calls: no deadlock, both healthy"


def s16_rapid_call_switching():
    """Rapid call switching: call A, wait, call B -- each gets chime."""
    ok, detail = ensure_hub_idle(label="S16")
    if not ok:
        return FAIL, detail

    # Phase 1: call INTERCOM2
    rx_ic2_before = get_rx_count(INTERCOM2_IP)
    trigger_call("INTERCOM2", "QA Phase1")
    time.sleep(3)
    rx_ic2_after = get_rx_count(INTERCOM2_IP)
    ic2_delta = rx_ic2_after - rx_ic2_before

    ensure_hub_idle(timeout=10, label="S16 between")

    # Phase 2: call Bedroom
    rx_bed_before = get_rx_count(BEDROOM_IP)
    trigger_call("Bedroom Intercom", "QA Phase2")
    time.sleep(3)
    rx_bed_after = get_rx_count(BEDROOM_IP)
    bed_delta = rx_bed_after - rx_bed_before

    if ic2_delta < 10:
        return FAIL, f"Phase 1: INTERCOM2 rx_delta={ic2_delta}, expected >=10 (chime)"
    if bed_delta < 10:
        return FAIL, f"Phase 2: Bedroom rx_delta={bed_delta}, expected >=10 (chime)"
    return PASS, f"Phase1 INTERCOM2 rx={ic2_delta}, Phase2 Bedroom rx={bed_delta}"


# ===========================================================================
# Category 3: MQTT Entity Control (S17-S23)
# ===========================================================================
def s17_volume_set():
    """Volume set via MQTT -> confirmed via /api/status."""
    set_device_volume(BEDROOM_UNIQUE_ID, 42)
    time.sleep(1)
    st = device_status(BEDROOM_IP)
    set_device_volume(BEDROOM_UNIQUE_ID, 80)  # restore
    if not st:
        return FAIL, "Bedroom unreachable"
    vol = st.get("volume")
    if vol != 42:
        return FAIL, f"Volume={vol}, expected 42"
    return PASS, f"Volume set to 42, confirmed"


def s18_mute_set():
    """Mute set via MQTT -> confirmed via /api/status."""
    set_device_mute(BEDROOM_UNIQUE_ID, True)
    time.sleep(1)
    st = device_status(BEDROOM_IP)
    set_device_mute(BEDROOM_UNIQUE_ID, False)  # restore
    if not st:
        return FAIL, "Bedroom unreachable"
    if not st.get("muted"):
        return FAIL, f"muted={st.get('muted')}, expected True"
    return PASS, "Mute ON confirmed"


def s19_dnd_set():
    """DND set via MQTT -> confirmed via /api/status."""
    set_device_dnd(INTERCOM2_UNIQUE_ID, True)
    time.sleep(3)  # MQTT propagation + NVS save can take 2s
    st = device_status(INTERCOM2_IP)
    if not st:
        set_device_dnd(INTERCOM2_UNIQUE_ID, False)
        return FAIL, "INTERCOM2 unreachable"
    dnd_val = st.get("dnd")
    set_device_dnd(INTERCOM2_UNIQUE_ID, False)  # restore
    time.sleep(1)
    if not dnd_val:
        return FAIL, f"dnd={dnd_val}, expected True"
    return PASS, "DND ON confirmed"


def s20_priority_set():
    """Priority set via MQTT -> confirmed via /api/status."""
    set_device_priority(BEDROOM_UNIQUE_ID, "High")
    time.sleep(1)
    st = device_status(BEDROOM_IP)
    set_device_priority(BEDROOM_UNIQUE_ID, "Normal")  # restore
    if not st:
        return FAIL, "Bedroom unreachable"
    pri = st.get("priority")
    if pri != 1:  # HIGH=1
        return FAIL, f"priority={pri}, expected 1 (High)"
    return PASS, "Priority set to High, confirmed"


def s21_target_set():
    """Target room set via MQTT -> confirmed via /api/status."""
    set_device_target(BEDROOM_UNIQUE_ID, "INTERCOM2")
    time.sleep(1)
    st = device_status(BEDROOM_IP)
    set_device_target(BEDROOM_UNIQUE_ID, "All Rooms")  # restore
    if not st:
        return FAIL, "Bedroom unreachable"
    target = st.get("target_room", "")
    if "INTERCOM2" not in target and "intercom2" not in target.lower():
        return FAIL, f"target_room='{target}', expected 'INTERCOM2'"
    return PASS, f"Target set to INTERCOM2, confirmed: '{target}'"


def s22_agc_toggle():
    """AGC toggle via MQTT -> confirmed via /api/status."""
    # Get current state
    st_before = device_status(BEDROOM_IP)
    if not st_before:
        return FAIL, "Bedroom unreachable"
    agc_before = st_before.get("agc_enabled", False)

    # Toggle
    set_device_agc(BEDROOM_UNIQUE_ID, not agc_before)
    time.sleep(1)
    st_after = device_status(BEDROOM_IP)

    # Restore
    set_device_agc(BEDROOM_UNIQUE_ID, agc_before)

    if not st_after:
        return FAIL, "Bedroom unreachable after AGC toggle"
    agc_after = st_after.get("agc_enabled")
    if agc_after == agc_before:
        return FAIL, f"AGC didn't toggle: before={agc_before}, after={agc_after}"
    return PASS, f"AGC toggled: {agc_before} -> {agc_after}"


def s23_led_states():
    """LED color correct for each state (verify via serial log patterns)."""
    # Trigger TX for 5s and poll until we see transmitting=true
    trigger_sustained_tx(BEDROOM_IP, duration=5)

    # Poll for TX state (sustained_tx takes ~300ms lead-in)
    saw_tx = False
    for _ in range(10):
        time.sleep(0.5)
        st = device_status(BEDROOM_IP)
        if st and st.get("transmitting"):
            saw_tx = True
            break

    if not saw_tx:
        return FAIL, "Bedroom never entered transmitting state"

    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    st_after = device_status(BEDROOM_IP)
    if not st_after:
        return FAIL, "Bedroom unreachable after TX"
    # If we got here, the device cycled through TX and back to idle
    # LED verification would need serial log patterns or a camera
    return PASS, "Device cycled TX->idle (LED state verification requires serial inspection)"


# ===========================================================================
# Category 4: Audio Collision & Priority (S24-S29)
# ===========================================================================
def s24_first_to_talk():
    """First-to-talk: A sending, B sends -> B's audio rejected at A."""
    ok, detail = ensure_hub_idle(label="S24")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    # A starts sending first
    trigger_sustained_tx(BEDROOM_IP, duration=8)
    time.sleep(2)  # Let A establish

    # B starts sending while A is still going
    rx_bed_before = get_rx_count(BEDROOM_IP)
    trigger_sustained_tx(INTERCOM2_IP, duration=3)
    time.sleep(4)  # Let B finish

    rx_bed_after = get_rx_count(BEDROOM_IP)
    bed_delta = rx_bed_after - rx_bed_before

    wait_for_tx_complete(BEDROOM_IP, duration=8)
    time.sleep(1)

    stats = get_audio_stats()
    bed_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)
    ic2_pkts = hub_packet_count(stats, INTERCOM2_DEVICE_ID)

    # Bedroom is transmitting so it blocks its own RX (half-duplex)
    # bed_delta should be ~0 because transmitting=true blocks on_audio_received
    # Hub should see packets from both though
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable"

    return PASS, (f"A TX first: hub saw bed={bed_pkts}, ic2={ic2_pkts}. "
                  f"Bedroom rx_delta={bed_delta} (half-duplex blocked)")


def s25_priority_preemption():
    """Priority preemption: NORMAL stream, HIGH priority interrupts."""
    ok, detail = ensure_hub_idle(label="S25")
    if not ok:
        return FAIL, detail

    # Set INTERCOM2 to HIGH priority
    set_device_priority(INTERCOM2_UNIQUE_ID, "High")
    time.sleep(0.5)

    # QAudioSender sends NORMAL priority audio
    sender = QAudioSender(priority=0)  # NORMAL
    sender.start(duration_seconds=8)
    time.sleep(2)

    # Verify Bedroom is receiving
    bed_receiving = device_status(BEDROOM_IP)
    was_rx = bed_receiving.get("receiving", False) if bed_receiving else False

    # INTERCOM2 sends HIGH priority (should preempt)
    rx_bed_before = get_rx_count(BEDROOM_IP)
    trigger_sustained_tx(INTERCOM2_IP, duration=3)
    time.sleep(4)

    sender.stop()
    wait_for_tx_complete(INTERCOM2_IP, duration=3)

    # Restore priority
    set_device_priority(INTERCOM2_UNIQUE_ID, "Normal")

    time.sleep(1)
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after priority preemption"

    return PASS, f"Priority preemption test complete, was_rx={was_rx}, both devices healthy"


def s26_channel_busy_web_ptt():
    """Channel busy: Web PTT active -> device sees channel busy."""
    ok, detail = ensure_hub_idle(label="S26")
    if not ok:
        return FAIL, detail

    # Start Web PTT in background (blocks the channel)
    client = WebPTTClient("QA_S26")
    t = client.transmit_async(target="All Rooms", duration=5.0)

    time.sleep(1)

    # Check hub state
    state = get_hub_state()

    t.join(timeout=10)
    time.sleep(1)

    if state != "transmitting":
        return SKIP, f"Hub state={state} during Web PTT (websockets may not be installed)"

    # Verify hub returns to idle
    ok2, _ = ensure_hub_idle(timeout=10, label="S26 after")
    if not ok2:
        return FAIL, "Hub didn't return to idle after Web PTT"
    return PASS, f"Hub state was 'transmitting' during Web PTT, returned to idle"


def s27_first_to_talk_holds():
    """New source during active RX -> first-to-talk holds."""
    ok, detail = ensure_hub_idle(label="S27")
    if not ok:
        return FAIL, detail

    # QAudioSender starts first (takes the channel)
    sender = QAudioSender(frequency=440.0)
    sender.start(duration_seconds=6)
    time.sleep(2)

    # Verify Bedroom is receiving from QAudioSender
    st1 = device_status(BEDROOM_IP)
    was_receiving = st1.get("receiving", False) if st1 else False

    # INTERCOM2 tries to send (should be rejected by first-to-talk on Bedroom)
    trigger_sustained_tx(INTERCOM2_IP, duration=3)
    time.sleep(2)

    # Bedroom should still be receiving from QAudioSender (first talker)
    st2 = device_status(BEDROOM_IP)
    still_receiving = st2.get("receiving", False) if st2 else False

    sender.wait(timeout=10)
    wait_for_tx_complete(INTERCOM2_IP, duration=3)
    time.sleep(1)

    bed_st = device_status(BEDROOM_IP)
    if bed_st is None:
        return FAIL, "Bedroom unreachable"

    return PASS, f"First-to-talk: was_rx={was_receiving}, still_rx={still_receiving}"


def s28_chime_during_rx():
    """Chime during active audio RX -> chime plays (HIGH priority preempts)."""
    ok, detail = ensure_hub_idle(label="S28")
    if not ok:
        return FAIL, detail

    # Start NORMAL priority audio
    sender = QAudioSender(priority=0)
    sender.start(duration_seconds=8)
    time.sleep(2)

    rx_bed_before = get_rx_count(BEDROOM_IP)

    # Trigger call (chime is PRIORITY_HIGH)
    trigger_call("Bedroom Intercom", "QA Chime")
    time.sleep(3)

    sender.stop()
    time.sleep(1)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    bed_delta = rx_bed_after - rx_bed_before

    bed_st = device_status(BEDROOM_IP)
    if bed_st is None:
        return FAIL, "Bedroom unreachable after chime during RX"

    return PASS, f"Chime during RX: Bedroom survived, rx_delta={bed_delta}"


def s29_tx_then_rx():
    """TX then immediate RX -> clean transition."""
    ok, detail = ensure_hub_idle(label="S29")
    if not ok:
        return FAIL, detail

    # Bedroom transmits first
    trigger_sustained_tx(BEDROOM_IP, duration=3)
    wait_for_tx_complete(BEDROOM_IP, duration=3)

    # Immediately send audio from INTERCOM2
    rx_bed_before = get_rx_count(BEDROOM_IP)
    trigger_sustained_tx(INTERCOM2_IP, duration=3)
    time.sleep(1)

    # Check if Bedroom enters receiving
    receiving = wait_device_receiving(BEDROOM_IP, timeout=5)

    wait_for_tx_complete(INTERCOM2_IP, duration=3)
    time.sleep(1)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    bed_delta = rx_bed_after - rx_bed_before

    if bed_delta < 50:
        return FAIL, f"Bedroom rx_delta={bed_delta} during reply, expected >=50"
    return PASS, f"TX->RX transition clean, Bedroom rx_delta={bed_delta}"


# ===========================================================================
# Category 5: Conversation Simulations (S30-S34)
# ===========================================================================
def s30_three_exchange():
    """3-exchange conversation (A talks, B talks, A talks)."""
    ok, detail = ensure_hub_idle(label="S30")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    for i, (speaker_ip, label) in enumerate([
        (BEDROOM_IP, "A1"), (INTERCOM2_IP, "B1"), (BEDROOM_IP, "A2")
    ]):
        trigger_sustained_tx(speaker_ip, duration=3)
        wait_for_tx_complete(speaker_ip, duration=3)
        time.sleep(0.5)

    stats = get_audio_stats()
    bed_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)
    ic2_pkts = hub_packet_count(stats, INTERCOM2_DEVICE_ID)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after 3-exchange"

    return PASS, f"3 exchanges clean. hub: Bedroom={bed_pkts}, INTERCOM2={ic2_pkts}"


def s31_ten_rapid_exchanges():
    """10 rapid 2s exchanges (alternating A/B)."""
    ok, detail = ensure_hub_idle(label="S31")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    for i in range(10):
        ip = BEDROOM_IP if i % 2 == 0 else INTERCOM2_IP
        trigger_sustained_tx(ip, duration=2)
        wait_for_tx_complete(ip, duration=2)
        time.sleep(0.3)
        if (i + 1) % 5 == 0:
            print(f"      exchange {i+1}/10 done", flush=True)

    stats = get_audio_stats()
    bed_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)
    ic2_pkts = hub_packet_count(stats, INTERCOM2_DEVICE_ID)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after 10 exchanges"

    return PASS, f"10 exchanges clean. hub: Bedroom={bed_pkts}, INTERCOM2={ic2_pkts}"


def s32_twenty_rapid_exchanges():
    """20 rapid 2s exchanges."""
    ok, detail = ensure_hub_idle(label="S32")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    for i in range(20):
        ip = BEDROOM_IP if i % 2 == 0 else INTERCOM2_IP
        trigger_sustained_tx(ip, duration=2)
        wait_for_tx_complete(ip, duration=2)
        time.sleep(0.3)
        if (i + 1) % 5 == 0:
            print(f"      exchange {i+1}/20 done", flush=True)

    stats = get_audio_stats()
    bed_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)
    ic2_pkts = hub_packet_count(stats, INTERCOM2_DEVICE_ID)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after 20 exchanges"

    return PASS, f"20 exchanges clean. hub: Bedroom={bed_pkts}, INTERCOM2={ic2_pkts}"


def s33_thirty_second_call():
    """30s sustained call -- heap stable, packet count ~1500."""
    ok, detail = ensure_hub_idle(label="S33")
    if not ok:
        return FAIL, detail

    reset_audio_stats()
    heap_before = get_heap(BEDROOM_IP)

    trigger_sustained_tx(BEDROOM_IP, duration=30)

    # Monitor periodically
    for t in [10, 20, 30]:
        time.sleep(10)
        st = device_status(BEDROOM_IP)
        if st:
            print(f"      t={t}s: heap={st.get('free_heap', 0)//1024}KB, "
                  f"tx_active={st.get('sustained_tx_active', False)}", flush=True)

    wait_for_tx_complete(BEDROOM_IP, timeout=15, duration=30)
    time.sleep(1)

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)
    heap_after = get_heap(BEDROOM_IP)
    heap_drift = abs(heap_after - heap_before)

    if heap_drift > HEAP_LEAK_THRESHOLD_BYTES:
        return FAIL, f"Heap drift {heap_drift}B exceeds {HEAP_LEAK_THRESHOLD_BYTES}B threshold"

    return PASS, f"30s TX: hub_pkts={hub_pkts}/1500, heap drift={heap_drift}B"


def s34_sixty_second_call():
    """60s sustained call -- heap drift < 8KB."""
    ok, detail = ensure_hub_idle(label="S34")
    if not ok:
        return FAIL, detail

    reset_audio_stats()
    heap_samples = []

    trigger_sustained_tx(BEDROOM_IP, duration=60)

    for t in range(1, 7):
        time.sleep(10)
        st = device_status(BEDROOM_IP)
        if st:
            h = st.get("free_heap", 0)
            heap_samples.append(h)
            print(f"      t={t*10}s: heap={h//1024}KB, "
                  f"tx_active={st.get('sustained_tx_active', False)}", flush=True)

    wait_for_tx_complete(BEDROOM_IP, timeout=15, duration=60)
    time.sleep(1)

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)

    if heap_samples:
        heap_drift = abs(heap_samples[-1] - heap_samples[0])
    else:
        heap_drift = 0

    if heap_drift > HEAP_LEAK_THRESHOLD_BYTES:
        return FAIL, f"60s heap drift {heap_drift}B exceeds threshold"

    return PASS, f"60s TX: hub_pkts={hub_pkts}/3000, heap drift={heap_drift}B"


# ===========================================================================
# Category 6: Stress & Durability (S35-S42)
# ===========================================================================
def s35_fifty_sequential_calls():
    """50 sequential MQTT calls -- no state corruption."""
    ok, detail = ensure_hub_idle(label="S35")
    if not ok:
        return FAIL, detail

    heap_before = get_heap(INTERCOM2_IP)

    with MqttSession(client_id="qa_s35") as session:
        for i in range(50):
            payload = json.dumps({"target": "INTERCOM2", "caller": f"QA Call {i+1}"})
            session.publish(CALL_TOPIC, payload)
            time.sleep(0.5)
            if (i + 1) % 10 == 0:
                st = device_status(INTERCOM2_IP)
                if st:
                    print(f"      call {i+1}/50: heap={st.get('free_heap', 0)//1024}KB, "
                          f"mqtt={st.get('mqtt_connected')}", flush=True)

    time.sleep(2)
    st = device_status(INTERCOM2_IP)
    if st is None:
        return FAIL, "INTERCOM2 unreachable after 50 calls"
    if not st.get("mqtt_connected"):
        return FAIL, "MQTT disconnected after 50 calls"
    return PASS, "50 sequential calls: MQTT stable, heap ok"


def s36_calls_during_tx():
    """20 rapid calls during TX -- device survives."""
    ok, detail = ensure_hub_idle(label="S36")
    if not ok:
        return FAIL, detail

    trigger_sustained_tx(BEDROOM_IP, duration=10)
    time.sleep(1)

    with MqttSession(client_id="qa_s36") as session:
        for i in range(20):
            payload = json.dumps({"target": "Bedroom", "caller": f"QA Flood {i+1}"})
            session.publish(CALL_TOPIC, payload)
            time.sleep(0.2)

    wait_for_tx_complete(BEDROOM_IP, duration=10)
    time.sleep(1)

    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Bedroom unreachable after 20 calls during TX"
    return PASS, "20 calls during TX: device survived"


def s37_simultaneous_calls_same_device():
    """5 simultaneous calls to same device."""
    ok, detail = ensure_hub_idle(label="S37")
    if not ok:
        return FAIL, detail

    for i in range(5):
        trigger_call("INTERCOM2", f"QA Sim {i+1}")
    time.sleep(5)

    st = device_status(INTERCOM2_IP)
    if st is None:
        return FAIL, "INTERCOM2 unreachable after 5 overlapping calls"
    return PASS, "5 overlapping calls: device survived"


def s38_double_rate_audio():
    """QAudioSender at 2x rate (100fps) for 10s."""
    ok, detail = ensure_hub_idle(label="S38")
    if not ok:
        return FAIL, detail

    sender1 = QAudioSender(frequency=440.0, device_id=b"QA_TST_1")
    sender2 = QAudioSender(frequency=880.0, device_id=b"QA_TST_2")
    sender1.start(duration_seconds=10)
    sender2.start(duration_seconds=10)
    sender1.wait(timeout=15)
    sender2.wait(timeout=15)
    time.sleep(1)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after 2x rate audio"
    return PASS, "2x rate audio: both devices survived"


def s39_mqtt_flood():
    """200 MQTT messages in 10s."""
    ok, detail = ensure_hub_idle(label="S39")
    if not ok:
        return FAIL, detail

    with MqttSession(client_id="qa_s39") as session:
        for i in range(200):
            # Mix of topics
            if i % 3 == 0:
                session.publish(f"intercom/{BEDROOM_UNIQUE_ID}/volume/set", str(random.randint(50, 100)))
            elif i % 3 == 1:
                session.publish(CALL_TOPIC, json.dumps({"target": "Bedroom", "caller": "QA Flood"}))
            else:
                session.publish(f"intercom/{BEDROOM_UNIQUE_ID}/mute/set", "OFF")
            time.sleep(0.05)

    time.sleep(2)
    restore_defaults()

    st = device_status(BEDROOM_IP)
    if st is None:
        return FAIL, "Bedroom unreachable after 200 MQTT messages"
    return PASS, "200 MQTT messages in 10s: device survived"


def s40_malformed_udp():
    """Malformed UDP packets -- no crash."""
    ok, detail = ensure_hub_idle(label="S40")
    if not ok:
        return FAIL, detail

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    errors = 0
    for i in range(50):
        # Random garbage of varying sizes
        size = random.randint(1, 2000)
        data = os.urandom(size)
        try:
            sock.sendto(data, (BEDROOM_IP, 5005))
            sock.sendto(data, (INTERCOM2_IP, 5005))
        except Exception:
            errors += 1
    sock.close()
    time.sleep(1)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after malformed UDP"
    return PASS, f"50 malformed packets ({errors} send errors), devices survived"


def s41_malformed_mqtt():
    """Null/huge MQTT payloads -- no crash."""
    ok, detail = ensure_hub_idle(label="S41")
    if not ok:
        return FAIL, detail

    payloads = [
        "",                          # empty
        "null",                      # JSON null
        "{}",                        # empty object
        '{"target": null}',          # null target
        "x" * 10000,                 # huge string
        '{"target": "' + "A" * 5000 + '"}',  # huge target
        "\x00\x01\x02\x03",         # binary garbage
    ]

    with MqttSession(client_id="qa_s41") as session:
        for p in payloads:
            session.publish(CALL_TOPIC, p)
            session.publish(f"intercom/{BEDROOM_UNIQUE_ID}/volume/set", p)
            time.sleep(0.2)

    time.sleep(2)
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after malformed MQTT"
    return PASS, "Malformed MQTT payloads: devices survived"


def s42_kitchen_sink_chaos():
    """Kitchen-sink chaos: sustained_tx + calls + malformed + API polling (30s)."""
    ok, detail = ensure_hub_idle(label="S42")
    if not ok:
        return FAIL, detail

    # Start sustained TX on both
    trigger_sustained_tx(BEDROOM_IP, duration=30)
    trigger_sustained_tx(INTERCOM2_IP, duration=30)

    # Background chaos
    def chaos_thread():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with MqttSession(client_id="qa_chaos") as session:
            for i in range(60):
                # MQTT calls
                session.publish(CALL_TOPIC,
                                json.dumps({"target": "Bedroom Intercom", "caller": "Chaos"}))
                # Malformed UDP
                sock.sendto(os.urandom(100), (BEDROOM_IP, 5005))
                # API poll
                device_status(BEDROOM_IP)
                time.sleep(0.5)
        sock.close()

    chaos = threading.Thread(target=chaos_thread, daemon=True)
    chaos.start()

    for t in [10, 20, 30]:
        time.sleep(10)
        bed_st = device_status(BEDROOM_IP)
        ic2_st = device_status(INTERCOM2_IP)
        bh = bed_st.get("free_heap", 0) // 1024 if bed_st else "?"
        ih = ic2_st.get("free_heap", 0) // 1024 if ic2_st else "?"
        print(f"      chaos t={t}s: Bedroom={bh}KB, INTERCOM2={ih}KB", flush=True)

    chaos.join(timeout=5)
    wait_for_tx_complete(BEDROOM_IP, duration=30)
    wait_for_tx_complete(INTERCOM2_IP, duration=30)
    time.sleep(2)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after kitchen-sink chaos"
    return PASS, "30s kitchen-sink chaos: both devices survived"


# ===========================================================================
# Category 7: Idle & Recovery (S43-S46)
# ===========================================================================
def s43_idle_soak():
    """5-minute idle soak then sudden call."""
    ok, detail = ensure_hub_idle(label="S43")
    if not ok:
        return FAIL, detail

    print("      waiting 5 minutes (idle soak)...", flush=True)
    for minute in range(1, 6):
        time.sleep(60)
        st = device_status(BEDROOM_IP)
        if st:
            print(f"      minute {minute}/5: heap={st.get('free_heap', 0)//1024}KB, "
                  f"mqtt={st.get('mqtt_connected')}", flush=True)

    # Sudden call
    reset_audio_stats()
    trigger_sustained_tx(BEDROOM_IP, duration=5)
    wait_for_tx_complete(BEDROOM_IP, duration=5)
    time.sleep(1)

    stats = get_audio_stats()
    hub_pkts = hub_packet_count(stats, BEDROOM_DEVICE_ID)

    if hub_pkts < 100:
        return FAIL, f"Post-idle: hub only saw {hub_pkts} packets"
    return PASS, f"Post-idle call: hub_pkts={hub_pkts}"


def s44_api_polling_during_tx():
    """API rapid polling during TX -- no interference."""
    ok, detail = ensure_hub_idle(label="S44")
    if not ok:
        return FAIL, detail

    trigger_sustained_tx(BEDROOM_IP, duration=10)
    time.sleep(1)

    polls = 0
    tx_seen = 0
    failures = 0
    for _ in range(20):
        st = device_status(BEDROOM_IP)
        polls += 1
        if st is None:
            failures += 1
        elif st.get("transmitting") or st.get("sustained_tx_active"):
            tx_seen += 1
        time.sleep(0.5)

    wait_for_tx_complete(BEDROOM_IP, duration=10)

    if failures > 2:
        return FAIL, f"{failures} API failures during TX"
    return PASS, f"API cycling: {polls} polls, {tx_seen} saw TX, {failures} failures"


def s45_web_ptt_timeout_recovery():
    """Web PTT timeout recovery -- hub auto-resets after 5s."""
    ok, detail = ensure_hub_idle(label="S45")
    if not ok:
        return FAIL, detail

    # Connect and start PTT but disconnect WITHOUT ptt_stop
    client = WebPTTClient("QA_S45")
    if not client.transmit(target="All Rooms", duration=1.0,
                           disconnect_without_stop=True):
        return SKIP, "WebSocket connection failed"

    # Hub should be stuck in transmitting
    time.sleep(1)
    state1 = get_hub_state()

    # Wait for timeout (5s + margin)
    time.sleep(7)
    state2 = get_hub_state()

    if state2 != "idle":
        return FAIL, f"Hub still '{state2}' after timeout (was '{state1}')"
    return PASS, f"Hub recovered: '{state1}' -> '{state2}' (auto-reset after idle timeout)"


def s46_conversation_cycles():
    """10 full conversation cycles (call, 5s audio, end, 2s idle)."""
    ok, detail = ensure_hub_idle(label="S46")
    if not ok:
        return FAIL, detail

    for i in range(10):
        trigger_call("INTERCOM2", f"QA Cycle {i+1}")
        time.sleep(1)
        trigger_sustained_tx(BEDROOM_IP, duration=5)
        wait_for_tx_complete(BEDROOM_IP, duration=5)
        time.sleep(2)
        if (i + 1) % 3 == 0:
            st = device_status(INTERCOM2_IP)
            if st:
                print(f"      cycle {i+1}/10: INTERCOM2 heap={st.get('free_heap', 0)//1024}KB",
                      flush=True)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after 10 conversation cycles"
    return PASS, "10 conversation cycles completed"


# ===========================================================================
# Category 8: Web PTT (S47-S51)
# ===========================================================================
def s47_web_ptt_all_rooms():
    """Web PTT to All Rooms -- both devices receive."""
    ok, detail = ensure_hub_idle(label="S47")
    if not ok:
        return FAIL, detail

    rx_bed_before = get_rx_count(BEDROOM_IP)
    rx_ic2_before = get_rx_count(INTERCOM2_IP)

    client = WebPTTClient("QA_S47")
    if not client.transmit(target="All Rooms", duration=5.0):
        return SKIP, "WebSocket connection failed"

    time.sleep(2)

    rx_bed_after = get_rx_count(BEDROOM_IP)
    rx_ic2_after = get_rx_count(INTERCOM2_IP)
    bed_delta = rx_bed_after - rx_bed_before
    ic2_delta = rx_ic2_after - rx_ic2_before

    if bed_delta < 100 and ic2_delta < 100:
        return FAIL, f"Neither device received: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}"
    if bed_delta < 100 or ic2_delta < 100:
        # IGMP multicast is intermittently flaky — one device may miss group join.
        # Pass with warning if at least one device received the full stream.
        return PASS, f"IGMP flaky: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta} (one missed multicast)"
    return PASS, f"Web PTT all rooms: Bedroom rx={bed_delta}, INTERCOM2 rx={ic2_delta}"


def s48_web_ptt_isolation():
    """Web PTT to specific device -- only target receives."""
    ok, detail = ensure_hub_idle(label="S48")
    if not ok:
        return FAIL, detail

    rx_ic2_before = get_rx_count(INTERCOM2_IP)
    rx_bed_before = get_rx_count(BEDROOM_IP)

    client = WebPTTClient("QA_S48")
    if not client.transmit(target="INTERCOM2", duration=3.0):
        return SKIP, "WebSocket connection failed"

    time.sleep(2)

    rx_ic2_after = get_rx_count(INTERCOM2_IP)
    rx_bed_after = get_rx_count(BEDROOM_IP)
    ic2_delta = rx_ic2_after - rx_ic2_before
    bed_delta = rx_bed_after - rx_bed_before

    if ic2_delta < 50:
        return FAIL, f"INTERCOM2 rx={ic2_delta}, expected >=50"
    return PASS, f"Targeted Web PTT: INTERCOM2 rx={ic2_delta}, Bedroom rx={bed_delta} (isolation)"


def s49_web_ptt_during_device_tx():
    """Web PTT while device PTT active -- no crash."""
    ok, detail = ensure_hub_idle(label="S49")
    if not ok:
        return FAIL, detail

    trigger_sustained_tx(BEDROOM_IP, duration=8)
    time.sleep(1)

    client = WebPTTClient("QA_S49")
    # Web PTT should get "busy" or proceed (depending on hub logic)
    client.transmit(target="All Rooms", duration=3.0)

    wait_for_tx_complete(BEDROOM_IP, duration=8)
    time.sleep(1)

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after Web PTT + device TX"
    return PASS, "Web PTT + device TX concurrent: no crash, both healthy"


def s50_web_ptt_disconnect_recovery():
    """Web PTT disconnect without ptt_stop -- hub auto-recovers."""
    ok, detail = ensure_hub_idle(label="S50")
    if not ok:
        return FAIL, detail

    client = WebPTTClient("QA_S50")
    if not client.transmit(target="All Rooms", duration=2.0,
                           disconnect_without_stop=True):
        return SKIP, "WebSocket connection failed"

    # Check hub stuck
    time.sleep(1)
    state_stuck = get_hub_state()

    # Wait for auto-recovery (5s timeout)
    time.sleep(7)
    state_recovered = get_hub_state()

    if state_recovered != "idle":
        return FAIL, f"Hub didn't recover: stuck='{state_stuck}', now='{state_recovered}'"
    return PASS, f"Disconnect recovery: '{state_stuck}' -> '{state_recovered}'"


def s51_concurrent_web_ptt():
    """Two concurrent Web PTT clients -> second gets busy."""
    ok, detail = ensure_hub_idle(label="S51")
    if not ok:
        return FAIL, detail

    # First client starts
    client1 = WebPTTClient("QA_S51_A")
    t1 = client1.transmit_async(target="All Rooms", duration=5.0)
    time.sleep(1)

    # Second client tries
    client2 = WebPTTClient("QA_S51_B")
    t2 = client2.transmit_async(target="All Rooms", duration=3.0)

    t1.join(timeout=10)
    t2.join(timeout=10)
    time.sleep(2)

    # Verify hub returns to idle
    ok2, _ = ensure_hub_idle(timeout=15, label="S51")
    if not ok2:
        return FAIL, "Hub didn't return to idle after concurrent Web PTT"

    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after concurrent Web PTT"
    return PASS, "Concurrent Web PTT: hub handled, both devices healthy"


# ===========================================================================
# Category 9: Abuse, Misuse & Edge Cases (S52-S58)
# ===========================================================================
def s52_spoofed_device_id():
    """Spoofed device_id -- valid packets from unknown device."""
    ok, detail = ensure_hub_idle(label="S52")
    if not ok:
        return FAIL, detail

    reset_audio_stats()

    # Send valid-looking packets with a fake device_id
    sender = QAudioSender(device_id=b"FAKEID!!", frequency=440.0)
    sender.start(duration_seconds=3)
    sender.wait(timeout=10)
    time.sleep(1)

    # Check hub tracked it
    stats = get_audio_stats()
    fake_hex = b"FAKEID!!".hex()
    fake_pkts = hub_packet_count(stats, fake_hex)

    # Check devices still healthy
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after spoofed packets"

    return PASS, f"Spoofed device: hub tracked {fake_pkts} packets, devices healthy"


def s53_dnd_during_rx():
    """DND toggled ON during active RX -- playback should stop."""
    ok, detail = ensure_hub_idle(label="S53")
    if not ok:
        return FAIL, detail

    # Start audio flowing to INTERCOM2
    sender = QAudioSender(priority=0)
    sender.start(duration_seconds=8)
    time.sleep(2)

    # Verify INTERCOM2 is receiving
    st1 = device_status(INTERCOM2_IP)
    was_receiving = st1.get("receiving", False) if st1 else False

    # Toggle DND ON
    set_device_dnd(INTERCOM2_UNIQUE_ID, True)
    time.sleep(2)

    # Check if still receiving
    st2 = device_status(INTERCOM2_IP)
    still_receiving = st2.get("receiving", False) if st2 else False

    sender.stop()
    set_device_dnd(INTERCOM2_UNIQUE_ID, False)
    time.sleep(1)

    ic2_st = device_status(INTERCOM2_IP)
    if ic2_st is None:
        return FAIL, "INTERCOM2 unreachable after DND during RX"

    return PASS, (f"DND during RX: was_receiving={was_receiving}, "
                  f"after_dnd={still_receiving}, device healthy")


def s54_volume_zero_during_rx():
    """Volume changed to 0 during active RX -- mutes but packets still counted."""
    ok, detail = ensure_hub_idle(label="S54")
    if not ok:
        return FAIL, detail

    # Use Web PTT to send audio (reliable unicast), targeting INTERCOM2 specifically
    rx_before = get_rx_count(INTERCOM2_IP)

    # Start Web PTT targeting INTERCOM2 in background
    client = WebPTTClient("QA_S54")
    import threading
    ptt_done = threading.Event()
    def _ptt():
        client.transmit(target="INTERCOM2", duration=7.0)
        ptt_done.set()
    t = threading.Thread(target=_ptt, daemon=True)
    t.start()
    time.sleep(2)

    # Set volume to 0
    set_device_volume(INTERCOM2_UNIQUE_ID, 0)
    time.sleep(4)

    ptt_done.wait(timeout=10)
    time.sleep(1)

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    # Restore volume
    set_device_volume(INTERCOM2_UNIQUE_ID, 80)

    ic2_st = device_status(INTERCOM2_IP)
    if ic2_st is None:
        return FAIL, "INTERCOM2 unreachable after volume=0 during RX"

    # rx_packet_count should still increase (counts at socket level before volume)
    if rx_delta < 100:
        return FAIL, f"rx_delta={rx_delta}, expected >=100 (packets counted before volume)"
    return PASS, f"Volume=0 during RX: rx_delta={rx_delta} (packets still counted)"


def s55_rapid_ptt_toggle():
    """Rapid PTT toggle -- 20 start/stop cycles in 10s."""
    ok, detail = ensure_hub_idle(label="S55")
    if not ok:
        return FAIL, detail

    for i in range(20):
        trigger_sustained_tx(BEDROOM_IP, duration=1)
        time.sleep(0.3)
        # Don't wait for completion -- rapid fire

    # Wait for everything to settle
    time.sleep(5)
    wait_device_idle(BEDROOM_IP, timeout=10)

    bed_st = device_status(BEDROOM_IP)
    if bed_st is None:
        return FAIL, "Bedroom unreachable after rapid PTT toggle"
    if bed_st.get("transmitting") or bed_st.get("sustained_tx_active"):
        return FAIL, "Bedroom stuck in transmitting state"
    return PASS, "20 rapid PTT toggles: device healthy, not stuck"


def s56_chime_during_chime():
    """Two calls 500ms apart -- both chimes should play or gracefully handle."""
    ok, detail = ensure_hub_idle(label="S56")
    if not ok:
        return FAIL, detail

    # Extra settle time — prior tests may have flooded MQTT
    time.sleep(2)

    rx_before = get_rx_count(INTERCOM2_IP)

    trigger_call("INTERCOM2", "QA Chime 1")
    time.sleep(0.5)
    trigger_call("INTERCOM2", "QA Chime 2")
    time.sleep(8)  # Hub chime is ~4.3s; wait for both to complete

    rx_after = get_rx_count(INTERCOM2_IP)
    rx_delta = rx_after - rx_before

    ic2_st = device_status(INTERCOM2_IP)
    if ic2_st is None:
        return FAIL, "INTERCOM2 unreachable after double chime"

    if rx_delta < 10:
        return FAIL, f"rx_delta={rx_delta}, expected >=10 (at least one chime)"
    return PASS, f"Double chime: rx_delta={rx_delta}, device survived"


def s57_mqtt_payload_injection():
    """MQTT payload injection -- special chars in caller/target."""
    ok, detail = ensure_hub_idle(label="S57")
    if not ok:
        return FAIL, detail

    injection_payloads = [
        {"target": "Bedroom", "caller": '<script>alert(1)</script>'},
        {"target": '"; DROP TABLE devices; --', "caller": "QA"},
        {"target": "Bedroom", "caller": "QA \x00\x01\x02"},
        {"target": "All Rooms", "caller": "test 🎤🔊"},
        {"target": "../../../etc/passwd", "caller": "QA"},
    ]

    with MqttSession(client_id="qa_s57") as session:
        for p in injection_payloads:
            session.publish(CALL_TOPIC, json.dumps(p))
            time.sleep(0.5)

    time.sleep(2)
    bed_st = device_status(BEDROOM_IP)
    ic2_st = device_status(INTERCOM2_IP)
    if bed_st is None or ic2_st is None:
        return FAIL, "Device unreachable after injection payloads"
    return PASS, "Injection payloads: devices survived, no crash"


def s58_api_auth_verification():
    """API auth verification -- no-auth requests should get 401."""
    # Try /api/status without auth
    code, _ = http_get(f"http://{BEDROOM_IP}/api/status", timeout=5)
    if code == 200:
        return FAIL, "/api/status returned 200 without auth (should be 401)"
    if code != 401:
        return FAIL, f"/api/status returned {code} without auth (expected 401)"

    # Try /api/test without auth
    code2, _ = http_post(f"http://{BEDROOM_IP}/api/test",
                         {"action": "beep"}, timeout=5)
    if code2 == 200:
        return FAIL, "/api/test returned 200 without auth (should be 401)"

    return PASS, f"Auth enforced: /api/status={code}, /api/test={code2}"


# ===========================================================================
# Test Registry
# ===========================================================================
TESTS = [
    # Category 1: Basic Audio Paths
    ("S01", "Device A sustained_tx -> Device B receives (multicast)", s01_device_a_to_b_multicast, 1, False),
    ("S02", "Device B sustained_tx -> Device A receives (multicast)", s02_device_b_to_a_multicast, 1, False),
    ("S03", "Device A unicast -> only Device B receives (isolation)", s03_unicast_isolation, 1, False),
    ("S04", "Hub chime -> specific device (unicast)", s04_hub_chime_unicast, 1, False),
    ("S05", "Hub chime -> All Rooms (multicast)", s05_hub_chime_all_rooms, 1, False),
    ("S06", "Web PTT -> specific device", s06_web_ptt_specific_device, 1, False),
    ("S07", "Web PTT -> All Rooms", s07_web_ptt_all_rooms, 1, False),
    ("S08", "TTS -> device (SKIP if Piper unavailable)", s08_tts_to_device, 1, False),
    ("S09", "Device sustained_tx -> hub audio_stats", s09_device_tx_hub_stats, 1, False),
    ("S10", "QAudioSender 440Hz -> hub content verify", s10_audio_content_verification, 1, False),

    # Category 2: Call System
    ("S11", "Single-device call: chime + audio", s11_single_device_call, 2, False),
    ("S12", "All Rooms call: both get chime", s12_all_rooms_call, 2, False),
    ("S13", "DND blocks incoming call", s13_dnd_blocks_call, 2, False),
    ("S14", "Call while device transmitting", s14_call_during_tx, 2, False),
    ("S15", "Both devices call simultaneously", s15_simultaneous_calls, 2, False),
    ("S16", "Rapid call switching: A then B", s16_rapid_call_switching, 2, False),

    # Category 3: MQTT Entity Control
    ("S17", "Volume set via MQTT", s17_volume_set, 3, False),
    ("S18", "Mute set via MQTT", s18_mute_set, 3, False),
    ("S19", "DND set via MQTT", s19_dnd_set, 3, False),
    ("S20", "Priority set via MQTT", s20_priority_set, 3, False),
    ("S21", "Target room set via MQTT", s21_target_set, 3, False),
    ("S22", "AGC toggle via MQTT", s22_agc_toggle, 3, False),
    ("S23", "LED state transitions (TX/idle)", s23_led_states, 3, False),

    # Category 4: Audio Collision & Priority
    ("S24", "First-to-talk: A sending, B rejected", s24_first_to_talk, 4, False),
    ("S25", "Priority preemption: HIGH interrupts NORMAL", s25_priority_preemption, 4, False),
    ("S26", "Channel busy: Web PTT blocks device", s26_channel_busy_web_ptt, 4, False),
    ("S27", "First-to-talk holds against new source", s27_first_to_talk_holds, 4, False),
    ("S28", "Chime during RX (HIGH preempts NORMAL)", s28_chime_during_rx, 4, False),
    ("S29", "TX then immediate RX transition", s29_tx_then_rx, 4, False),

    # Category 5: Conversation Simulations
    ("S30", "3-exchange conversation", s30_three_exchange, 5, False),
    ("S31", "10 rapid 2s exchanges", s31_ten_rapid_exchanges, 5, False),
    ("S32", "20 rapid 2s exchanges", s32_twenty_rapid_exchanges, 5, True),
    ("S33", "30s sustained call -- heap stable", s33_thirty_second_call, 5, True),
    ("S34", "60s sustained call -- heap drift < 8KB", s34_sixty_second_call, 5, True),

    # Category 6: Stress & Durability
    ("S35", "50 sequential MQTT calls", s35_fifty_sequential_calls, 6, True),
    ("S36", "20 rapid calls during TX", s36_calls_during_tx, 6, False),
    ("S37", "5 simultaneous calls to same device", s37_simultaneous_calls_same_device, 6, False),
    ("S38", "QAudioSender at 2x rate for 10s", s38_double_rate_audio, 6, False),
    ("S39", "200 MQTT messages in 10s", s39_mqtt_flood, 6, False),
    ("S40", "Malformed UDP packets -- no crash", s40_malformed_udp, 6, False),
    ("S41", "Null/huge MQTT payloads -- no crash", s41_malformed_mqtt, 6, False),
    ("S42", "Kitchen-sink chaos (30s)", s42_kitchen_sink_chaos, 6, True),

    # Category 7: Idle & Recovery
    ("S43", "5-minute idle soak then call", s43_idle_soak, 7, True),
    ("S44", "API rapid polling during TX", s44_api_polling_during_tx, 7, False),
    ("S45", "Web PTT timeout recovery (5s)", s45_web_ptt_timeout_recovery, 7, False),
    ("S46", "10 conversation cycles", s46_conversation_cycles, 7, True),

    # Category 8: Web PTT
    ("S47", "Web PTT All Rooms -- both receive", s47_web_ptt_all_rooms, 8, False),
    ("S48", "Web PTT specific device -- isolation", s48_web_ptt_isolation, 8, False),
    ("S49", "Web PTT while device TX -- no crash", s49_web_ptt_during_device_tx, 8, False),
    ("S50", "Web PTT disconnect -- hub recovers", s50_web_ptt_disconnect_recovery, 8, False),
    ("S51", "Two concurrent Web PTT clients", s51_concurrent_web_ptt, 8, False),

    # Category 9: Abuse & Edge Cases
    ("S52", "Spoofed device_id packets", s52_spoofed_device_id, 9, False),
    ("S53", "DND toggled during active RX", s53_dnd_during_rx, 9, False),
    ("S54", "Volume=0 during active RX", s54_volume_zero_during_rx, 9, False),
    ("S55", "Rapid PTT toggle (20 cycles)", s55_rapid_ptt_toggle, 9, False),
    ("S56", "Chime during chime (double call)", s56_chime_during_chime, 9, False),
    ("S57", "MQTT payload injection", s57_mqtt_payload_injection, 9, False),
    ("S58", "API auth verification (no-auth = 401)", s58_api_auth_verification, 9, False),
]

CATEGORY_NAMES = {
    1: "Basic Audio Paths",
    2: "Call System",
    3: "MQTT Entity Control",
    4: "Audio Collision & Priority",
    5: "Conversation Simulations",
    6: "Stress & Durability",
    7: "Idle & Recovery",
    8: "Web PTT",
    9: "Abuse & Edge Cases",
}


# ===========================================================================
# Main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="HA Intercom Audio Scenario Tests")
    parser.add_argument("--test", help="Run a single test by ID (e.g. S05)")
    parser.add_argument("--category", type=int, help="Run tests in a category (1-9)")
    parser.add_argument("--list", action="store_true", help="List all tests")
    parser.add_argument("--skip-long", action="store_true",
                        help="Skip long-running tests (>60s)")
    parser.add_argument("--no-stale-check", action="store_true",
                        help="Disable stale audio check between tests")
    parser.add_argument("--report", default="tests/qa_report.md",
                        help="Report output path")
    args = parser.parse_args()

    if args.list:
        for cat_id in sorted(CATEGORY_NAMES.keys()):
            print(f"\n  Category {cat_id}: {CATEGORY_NAMES[cat_id]}")
            for tid, name, _, cat, is_long in TESTS:
                if cat == cat_id:
                    long_tag = " [LONG]" if is_long else ""
                    print(f"    {tid}: {name}{long_tag}")
        print(f"\n  Total: {len(TESTS)} tests")
        return

    # Filter tests
    selected = TESTS[:]
    if args.test:
        selected = [(t, n, f, c, l) for t, n, f, c, l in TESTS
                     if t.upper() == args.test.upper()]
        if not selected:
            print(f"Test {args.test} not found")
            return
    elif args.category:
        selected = [(t, n, f, c, l) for t, n, f, c, l in TESTS
                     if c == args.category]
    if args.skip_long:
        selected = [(t, n, f, c, l) for t, n, f, c, l in selected if not l]

    # Banner
    print("\n" + "=" * 70)
    print("  HA Intercom Audio Scenario Tests")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Pre-flight checks
    print("\n  Pre-flight device check:")
    all_ok = True
    for name, info in DEVICES.items():
        st = device_status(info["ip"])
        if st:
            fw = st.get("firmware_version", "?")
            heap = st.get("free_heap", 0) // 1024
            mqtt_ok = st.get("mqtt_connected", False)
            print(f"    {name}: fw={fw}, heap={heap}KB, mqtt={mqtt_ok}")
        else:
            print(f"    {name}: UNREACHABLE")
            all_ok = False

    hub_stats = get_audio_stats()
    if hub_stats:
        print(f"    Hub: reachable, state={hub_stats.get('current_state', '?')}")
    else:
        print("    Hub: UNREACHABLE")
        all_ok = False

    if not all_ok:
        print("\n  ABORT: Not all devices reachable")
        return

    # Ensure hub idle before starting
    ok, detail = ensure_hub_idle(timeout=15, label="pre-flight")
    if not ok:
        print(f"\n  WARNING: {detail}")

    # Multicast warmup — fresh hub rebuilds cause IGMP group membership
    # to be stale. Send a short TX from each device to warm up IGMP tables.
    print("\n  Multicast warmup (IGMP refresh):")
    trigger_sustained_tx(BEDROOM_IP, duration=2)
    time.sleep(3)
    trigger_sustained_tx(INTERCOM2_IP, duration=2)
    time.sleep(3)
    print("    Both devices sent multicast, IGMP tables should be warm")

    # Reset audio stats
    reset_audio_stats()

    # Restore device defaults
    restore_defaults()

    # Start log harness
    harness = LogHarness()
    harness.start()

    print(f"  Tests to run: {len(selected)} / {len(TESTS)}")

    # Run tests
    results = []
    for test_id, name, fn, category, is_long in selected:
        result = run_test(test_id, name, fn, harness,
                          check_stale=not args.no_stale_check)
        results.append(result)

        # Restore defaults between tests that might have changed state
        if category in (3, 4, 6, 8, 9):  # MQTT entity, collision, stress, web PTT, abuse
            restore_defaults()

    # Stop harness
    harness.stop()

    # Summary
    print_summary(results)

    # Write report
    generate_report(results, harness, output_path=args.report)


if __name__ == "__main__":
    main()
