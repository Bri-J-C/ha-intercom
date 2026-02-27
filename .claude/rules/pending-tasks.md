# Pending Tasks

## Test Fixes — Low Effort
1. **S47 test tolerance** — IGMP firmware fix already deployed (v2.9.1 rejoin timer). Test just needs to accept PASS if either device receives multicast, not require both.
2. **S56 investigation** — Hub MQTT handler may drop calls after heavy traffic (S55 flood test). Needs investigation — could be resolved by v2.5.4 MQTT crash fix or still present.
3. **S58 not a bug** — `/api/status` responds without auth because `web_admin_password` is empty (never configured on test devices). By design for first-time setup.

## Active — To Implement (Audio)
4. **DSP Audio Pipeline** — Biquad filters (noise gate, HPF, compressor, voice EQ) using ESP-DSP library, between mic capture and Opus encode. Would also reduce feedback when devices are near each other.
5. **APLL Playback Rate Correction** — Hardware clock tuning to prevent audio drift during long streams. Reference: jorgenkraghjakobsen/snapclient

## Active — To Implement (Diagnostics)
6. **ESP32 Sequence Continuity Logging** — Firmware-side: log when received sequence numbers jump unexpectedly (e.g., 100→5000→101). Detects interleaved hub TX streams.

## Active — Needs Hardware
7. **ES8311 Codec Support** — Single-bus I2S duplex for integrated codecs. Requires hardware.

## Future Ideas
- Snapcast client mode (dual-purpose intercom/speaker)
- Voicemail recording when call unanswered
- Audio ducking for notifications during playback
- Multi-zone audio routing
- Opus streaming to Snapcast server
