# Project Log
_Last Updated: 2026-02-26 — Audio verification infrastructure built. Hub v2.5.7 deployed. 64-test suite passing. Pending commit._

## Currently In Progress

### Session 2026-02-26 (Latest) — Audio Verification Infrastructure

**STATUS: Hub v2.5.7 deployed + working. 64-test suite (was 59) with 5 new audio verification tests. All S60-S64 PASS. Category 1 (10/10) and Category 10 (5/5) verified. UNCOMMITTED — ready to commit.**

#### Completed This Session:
1. **Hub v2.5.6** — Fixed BUG-1 (chime concurrency) and BUG-2 (shared sequence_num) from code review. Added tx_lock to `_stream_chime_blocking()`, set `current_state` during chime streaming.
2. **Hub v2.5.7** — Audio capture infrastructure for QA:
   - `AudioCaptureBuffer` class — ring buffer (2000 frames, ~40s) storing Opus frames from RX + TX paths
   - `POST /api/audio_capture` — control: `{"action": "start|stop|clear"}`
   - `GET /api/audio_capture` — fetch frames with filters (direction, device_id, since, limit)
   - TX stats (`tx.packets`, `tx.errors`) added to `/api/audio_stats` response
   - TX counter reset in `POST /api/audio_stats`
   - Hooks in `receive_thread` (RX), `send_audio_packet` (TX), `_stream_chime_blocking` (TX)
3. **Test harness additions** (`test_harness.py`):
   - `check_sequence_continuity()` — analyzes seq_min/seq_max/packet_count from audio_stats
   - `start_audio_capture()` / `stop_audio_capture()` / `fetch_audio_capture()` — hub capture API wrappers
   - `AudioAnalyzer` class — decodes Opus via opuslib, FFT frequency detection, SNR calculation, RMS, clipping detection
4. **5 new audio verification tests** (Category 10):
   - S60: Hub TX capture + monotonic sequence verification → 201 frames, monotonic=True
   - S61: Hub TX vs device RX count → TX=214, RX=214, 0% loss
   - S62: ESP32 sustained_tx decode quality → 500 frames, RMS=616.5, non_silent=True
   - S63: QAudioSender 440Hz FFT → freq=440.0Hz exact match, SNR=48.9dB
   - S64: Hub chime decode quality → 214 frames, RMS=1133.3, non_silent=True
5. **Sequence continuity checks** added to existing tests S01, S02, S09, S10, S33, S34
6. **Bug triage** — reviewed all 5 bugs from prior code review:
   - BUG-1/BUG-2: Fixed in v2.5.6 (this session)
   - BUG-3/S58: Not a bug — `web_admin_password` not configured on test devices, by design for first-time setup
   - BUG-4: Already fixed — beep stops RX, flushes queue, stops I2S before playing
   - BUG-5: Already fixed in v2.5.5 — clears stale mobile entries on MQTT connect
7. **Updated pending-tasks.md** with clean task list

#### Test Results This Session:
- Category 10 (Audio Verification): **5/5 PASS**
- Category 1 (Basic Audio Paths with seq checks): **10/10 PASS**
- All new tests run individually and as category — all green

#### Deployed Versions:
- **Firmware**: v2.9.2 (Bedroom 10.0.0.15, INTERCOM2 10.0.0.14) — committed as v2.9.1 in git
- **Hub**: v2.5.7 (10.0.0.8) — **UNCOMMITTED**, deployed and working
- **Last commit**: `24817b7` (hub v2.5.5, firmware v2.9.1)

#### Files Modified (uncommitted):
- `intercom_hub/intercom_hub.py` — v2.5.5 → v2.5.7 (BUG-1/2 fix + AudioCaptureBuffer)
- `tests/test_harness.py` — AudioAnalyzer, check_sequence_continuity, capture helpers
- `tests/test_audio_scenarios.py` — S60-S64, seq checks on existing tests, 64 total
- `.claude/rules/pending-tasks.md` — updated task list

#### User-Reported Issues:
- **Microphone too sensitive** — INMP441 has hardcoded +6dB gain in `audio_input.c:160-164` (`>>12` + `*2`). No runtime adjustment. Added to pending tasks: configurable `mic_gain` NVS setting.
- **Acoustic feedback during tests** — When both devices on same desk, Device B speaker feeds back into Device A mic during sustained_tx. Half-duplex only mutes the transmitting device's speaker, not other devices'. Physical proximity issue, would be helped by DSP noise gate.
- **AGC test is shallow** — S22 only tests setting toggle, not actual audio effect. Added to pending tasks: functional AGC test using AudioCaptureBuffer.

#### Next Actions:
1. **Commit** v2.5.6 + v2.5.7 hub + test suite changes
2. Implement configurable mic gain (user priority)
3. AGC functional test
4. Investigate S56 (may already be fixed by v2.5.4 MQTT crash fix)
5. S47 test tolerance fix (trivial)

---

### Previous Session — Deep Code Review + Phone Fix (2026-02-26 earlier)

**STATUS: v2.9.1 firmware + v2.5.5 hub committed and pushed to origin/main (24817b7).**

Completed:
1. Hub v2.5.5 — Phone dynamic visibility fix
2. Deep code review of all 12 source files — 5 bugs found (all now resolved, see above)
3. Committed and pushed 4 commits (41c235c..24817b7)

### Previous Session — v2.8.9 Test Run 3 (55/58 PASS)

Test Run 3: 55 PASS / 3 FAIL (S47=IGMP timing, S56=hub MQTT after flood, S58=auth not set)
Fixes: task watchdog (vTaskDelay), room name, restore_defaults coverage, S56 timing tuning.

### Older Sessions
See MEMORY.md session history for full details.
