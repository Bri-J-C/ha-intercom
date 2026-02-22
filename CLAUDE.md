# HA Intercom Project - Claude Code Context

## Build/Deploy Commands

### Add-on (SSH to 10.0.0.8)
```bash
# Copy and rebuild (ALWAYS rebuild after changes)
scp intercom_hub/intercom_hub.py root@10.0.0.8:/addons/intercom_hub/
scp intercom_hub/www/* root@10.0.0.8:/addons/intercom_hub/www/
ssh root@10.0.0.8 "ha apps rebuild local_intercom_hub"

# View logs
ssh root@10.0.0.8 "ha apps logs local_intercom_hub --lines 30"
```

### ESP32 Firmware (PlatformIO)
```bash
cd firmware
/home/user/.pio-venv/bin/pio run                                        # Build
/home/user/.pio-venv/bin/pio run -t upload --upload-port /dev/ttyACM0   # Flash
/home/user/.pio-venv/bin/pio run -t fullclean                           # Full clean (needed after sdkconfig changes)
```

## Project Architecture

### Overview
A multi-node intercom system for Home Assistant. ESP32-S3 devices act as room intercoms with push-to-talk, while a Python add-on on HA acts as the central hub for MQTT coordination, web PTT clients, TTS, and audio routing.

### Audio Flow
```
TX Path: Microphone (INMP441) -> I2S -> 32-to-16bit conversion -> Opus encode -> UDP multicast/unicast
RX Path: UDP receive -> Opus decode -> Volume/mute scaling -> Mono-to-stereo -> I2S -> Speaker (MAX98357A)
Hub:     MQTT commands + WebSocket audio + TTS generation + Audio forwarding between web clients
```

### Key Protocol Details
- **Audio codec**: Opus at 32kbps, 16kHz mono, 20ms frames (320 samples)
- **Transport**: UDP multicast (224.0.0.100:5005) for broadcast, unicast for targeted rooms
- **Packet format**: 8-byte device_id + 4-byte sequence + variable Opus data
- **Control plane**: MQTT via Home Assistant broker
- **Web clients**: WebSocket (binary PCM, JSON control messages)
- **Collision avoidance**: First-to-talk with 500ms timeout

### intercom_hub/ (Home Assistant Add-on, Python)
- `intercom_hub.py` (v1.32.0): Main hub server
  - MQTT client for HA integration (auto-discovery, state sync, volume/mute/target control)
  - WebSocket server for web PTT clients (individual client tracking with client IDs)
  - Audio routing: ESP32<->ESP32, ESP32<->WebClient, WebClient<->WebClient, TTS->all
  - Mobile device auto-discovery from HA companion apps
  - TTS via HA `tts.speak` service with channel-busy waiting
  - `create_tx_socket()` / `create_rx_socket()` - UDP with `IP_MULTICAST_LOOP=0`
  - `is_channel_busy()` checks: web_ptt_active, current_state=="transmitting"/"receiving"
  - `publish_state(notify_web, source)` - targeted web client state notifications
  - `encode_and_broadcast()` - Opus encode + send + forward PCM to web clients
- `www/index.html`: Web PTT UI (gradient theme, call button, room selector)
- `www/ptt-v7.js`: Web PTT client JavaScript (WebSocket, AudioContext, mic capture)

### firmware/ (ESP32-S3 Firmware, C, PlatformIO/ESP-IDF)
| File | Purpose |
|------|---------|
| `main/main.c` | App entry, PTT logic, audio RX handler, chime, main loop |
| `main/audio_input.c` | I2S mic input (INMP441), 32->16bit conversion, PSRAM buffers |
| `main/audio_output.c` | I2S speaker output (MAX98357A), mono->stereo, volume, PSRAM buffers |
| `main/codec.c` | Opus encoder/decoder (PSRAM-allocated ~36KB), PLC, FEC support |
| `main/network.c` | WiFi STA/AP mode, UDP multicast/unicast, mDNS, multicast loopback disabled |
| `main/ha_mqtt.c` | MQTT client, HA auto-discovery, device tracking, room targeting, call notifications |
| `main/display.c` | SSD1306 OLED (I2C), room selector UI, cycle button, state display |
| `main/settings.c` | NVS storage with AES-256-GCM encryption (key from eFuse MAC) |
| `main/button.c` | BOOT button for PTT, WS2812 LED states |
| `main/webserver.c` | HTTP config portal, OTA updates |
| `main/discovery.c` | mDNS device discovery |
| `main/diagnostics.c` | Heap/task monitoring |
| `main/protocol.h` | Shared constants (sample rate, frame size, ports, packet format) |
| `main/chime_data.h` | Incoming call chime audio (PCM in flash) |

### Hardware Per Node
- ESP32-S3-DevKitC-1 (N8 or N8R2/N8R8 with PSRAM)
- INMP441 MEMS microphone (I2S Bus 0: SCK=4, WS=5, SD=6)
- MAX98357A I2S amplifier (I2S Bus 1: SCK=15, WS=16, SD=17)
- SSD1306 128x64 OLED display (I2C: SDA=8, SCL=9, addr=0x3C)
- WS2812 RGB LED (on BOOT button GPIO)
- Cycle button (GPIO 10) for room selection + long press for call

## Network Info
- Home Assistant server: 10.0.0.8
- Bedroom Intercom: 10.0.0.36
- INTERCOM2: 10.0.0.38 (weak WiFi — OTA must run from HA server: `scp firmware.bin root@10.0.0.8:/tmp/ && ssh root@10.0.0.8 "curl -F 'firmware=@/tmp/firmware.bin' http://10.0.0.38/update"`)
- Office Intercom: 10.0.0.41 (was offline)
- Multicast audio: 224.0.0.100:5005
- MQTT broker: 10.0.0.8 (HA default)

## Key Requirements
- **ALWAYS rebuild the add-on** after changes: `ha apps rebuild local_intercom_hub`
- **Update VERSION** in `intercom_hub.py` with each hub change
- **Update FIRMWARE_VERSION** in `firmware/main/protocol.h` with each firmware change
- **Ask before committing/pushing** to GitHub
- **Research the internet** instead of guessing when unsure
- **Make sure to keep add-on up to date** when making changes to esp firmware keep add on in sync. 
- **Read all relevant code** before making changes to understand the full picture
- **PSRAM config**: `sdkconfig.esp32s3` controls PSRAM (not just `sdkconfig.defaults`). After sdkconfig changes, run `pio run -t fullclean` before building
- **Encryption**: WiFi/MQTT/Web/AP passwords are AES-256-GCM encrypted in NVS. Key derived from eFuse MAC + salt via SHA-256. Backwards compatible with plaintext

## Current Features (v1.32.0 hub / v2.1.0 firmware)

### ESP32 Firmware
- Push-to-talk via BOOT button with first-to-talk collision avoidance
- Opus codec (32kbps VBR, complexity 5, PLC + FEC for packet loss)
- OLED room selector with cycle button (short press = next room, long press = call)
- Availability tracking (online/offline per device via MQTT LWT)
- Mobile device detection in room list
- WS2812 LED states: white=idle, cyan=TX, green=RX, red=muted, orange=busy
- Incoming call chime with LED flash
- Lead-in/trail-out silence frames for clean audio start/stop
- PSRAM support (Opus encoder/decoder + audio buffers in PSRAM if available)
- AES-256-GCM credential encryption in NVS
- Web config portal + OTA updates
- WiFi AP fallback mode for initial setup

### Hub Add-on
- WebSocket-based Web PTT with individual client IDs and registration
- Per-client state tracking (prevents all clients showing same state)
- Audio forwarding: ESP32<->Web, Web<->Web, TTS->Web+ESP32
- Mobile device auto-discovery from HA companion apps
- TTS with channel-busy waiting
- Call/notify between all node types
- Room selector dropdown with all discovered devices

### Web PTT (Browser)
- Gradient-themed UI with PTT button and call button
- Call button: dim at idle, cyan-purple gradient on hover, green when calling
- Room/device selector dropdown
- Connection status and PTT state indicators
- Device name input on first launch
- AudioContext + getUserMedia for mic capture
- Binary WebSocket for PCM audio

## Pending Enhancement Tasks

### Active (to implement)
1. **AEC (Echo Cancellation)** - ESP-SR library integration for acoustic echo cancellation. Reference: n-IA-hane/intercom-api esp_aec component
2. **DSP Audio Pipeline** - Biquad filters (noise gate, HPF, compressor, voice EQ) using ESP-DSP library between decode and I2S output
3. **APLL Playback Rate Correction** - Hardware clock tuning to prevent audio drift during long streams. Reference: jorgenkraghjakobsen/snapclient
4. **ES8311 Codec Support** - Single-bus I2S duplex for integrated codecs. Requires hardware

### Future Ideas
- Snapcast client mode (dual-purpose intercom/speaker)
- Voicemail recording when call unanswered
- Audio ducking for notifications during playback
- Multi-zone audio routing
- Opus streaming to Snapcast server

## Important Implementation Notes

### Audio Buffer Architecture
- DMA buffers: Managed internally by I2S driver (8 descriptors x FRAME_SIZE)
- Stereo conversion buffer: Dynamically allocated (PSRAM preferred, internal fallback)
- Raw mic buffer: Dynamically allocated (PSRAM preferred, internal fallback)
- Opus encoder/decoder state: ~36KB total, allocated in PSRAM when available
- `esp_ptr_external_ram()` from `esp_memory_utils.h` to check if pointer is in PSRAM
- `SPIRAM_IGNORE_NOTFOUND=y` ensures boards without PSRAM still boot

### Web Client State Routing
- Each web client has a unique client_id (device name or generated)
- `publish_state(notify_web=False)` prevents double-notifications
- `notify_targeted_web_client_state(target_device, state)` for per-client updates
- "All Rooms" broadcast detection: `target.lower() in ('all', 'all rooms')`

### Multicast Loopback Prevention
- `IP_MULTICAST_LOOP=0` set on TX socket in both hub and ESP32 firmware
- Prevents devices from receiving their own multicast packets

### Settings Encryption (ESP32)
- Encryption version byte + 12-byte IV + ciphertext + 16-byte GCM tag
- Key: SHA-256(salt + MAC address) - unique per device
- Encrypted fields: wifi_pass, mqtt_pass, web_pass, ap_pass
- Backwards compatible: detects plaintext vs encrypted on read
