# Pending Tasks

## Active — To Implement
1. **DSP Audio Pipeline** — Biquad filters (noise gate, HPF, compressor, voice EQ) using ESP-DSP library, between decode and I2S output
2. **APLL Playback Rate Correction** — Hardware clock tuning to prevent audio drift during long streams. Reference: jorgenkraghjakobsen/snapclient
3. **ES8311 Codec Support** — Single-bus I2S duplex for integrated codecs. Requires hardware

## Future Ideas
- Snapcast client mode (dual-purpose intercom/speaker)
- Voicemail recording when call unanswered
- Audio ducking for notifications during playback
- Multi-zone audio routing
- Opus streaming to Snapcast server
