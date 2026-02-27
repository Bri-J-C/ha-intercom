# Pending Tasks

## Uncommitted Work — Needs Commit
- **Hub v2.5.6** — tx_lock chime concurrency fix (BUG-1/BUG-2 from code review). Deployed.
- **Hub v2.5.7** — AudioCaptureBuffer, `/api/audio_capture`, TX stats in `/api/audio_stats`. Deployed.
- **Test suite (64 tests)** — 5 new audio verification tests (S60-S64), AudioAnalyzer, sequence continuity checks.

## Test Fixes — Low Effort
1. **S47 test tolerance** — IGMP firmware fix already deployed (v2.9.1 rejoin timer). Test just needs to accept PASS if either device receives multicast, not require both.
2. **S56 investigation** — Hub MQTT handler may drop calls after heavy traffic (S55 flood test). Needs investigation — could be resolved by v2.5.4 MQTT crash fix or still present.
3. **S58 not a bug** — `/api/status` responds without auth because `web_admin_password` is empty (never configured on test devices). By design for first-time setup.

## Active — To Implement (Audio)
4. **Configurable Mic Gain** — INMP441 too sensitive. Hardcoded `>>12` + `*2` (+6dB) in `audio_input.c:160-164`. Add `mic_gain` NVS setting (0.5x–4.0x) exposed as MQTT number entity.
5. **DSP Audio Pipeline** — Biquad filters (noise gate, HPF, compressor, voice EQ) using ESP-DSP library, between mic capture and Opus encode. Would also reduce feedback when devices are near each other.
6. **APLL Playback Rate Correction** — Hardware clock tuning to prevent audio drift during long streams. Reference: jorgenkraghjakobsen/snapclient

## Active — To Implement (Tests)
7. **AGC Functional Test** — S22 only tests setting toggle. Add test that sends quiet signal with AGC off, captures RMS at hub, then AGC on, captures again, verifies RMS increases. Uses AudioCaptureBuffer.
8. **ESP32 Sequence Continuity Logging** — Firmware-side: log when received sequence numbers jump unexpectedly (e.g., 100→5000→101). Detects interleaved hub TX streams.

## Active — Needs Hardware
9. **ES8311 Codec Support** — Single-bus I2S duplex for integrated codecs. Requires hardware.

## Future Ideas
- Snapcast client mode (dual-purpose intercom/speaker)
- Voicemail recording when call unanswered
- Audio ducking for notifications during playback
- Multi-zone audio routing
- Opus streaming to Snapcast server
