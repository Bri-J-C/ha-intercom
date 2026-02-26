# Architecture

## Overview
Multi-node intercom system for Home Assistant. ESP32-S3 devices are room intercoms with push-to-talk. A Python add-on on HA acts as the central hub for MQTT coordination, web PTT clients, TTS, and audio routing.

## Audio Flow
```
TX: Microphone (INMP441) → I2S → 32-to-16bit → Opus encode → UDP multicast/unicast
RX: UDP receive → Opus decode → Volume/mute scaling → Mono-to-stereo → I2S → Speaker (MAX98357A)
Hub: MQTT + WebSocket audio + TTS generation + Audio forwarding between web clients
```

## intercom_hub/ (Python Add-on)
- `intercom_hub.py` (v2.2.1): Main hub — MQTT, WebSocket, audio routing, TTS, mobile discovery
- `www/index.html`: Web PTT UI
- `www/ptt-v7.js`: Web PTT client (WebSocket, AudioContext, mic capture)
- Lovelace card: `intercom-ptt-card.js` v1.2.0

## firmware/ (ESP32-S3, C, PlatformIO/ESP-IDF)
| File | Purpose |
|---|---|
| `main/main.c` | App entry, PTT logic, audio RX, chime, main loop |
| `main/audio_input.c` | I2S mic (INMP441), 32→16bit, PSRAM buffers |
| `main/audio_output.c` | I2S speaker (MAX98357A), mono→stereo, volume |
| `main/codec.c` | Opus encoder/decoder (~36KB PSRAM), PLC, FEC |
| `main/network.c` | WiFi, UDP multicast/unicast, mDNS, DHCP hostname |
| `main/ha_mqtt.c` | MQTT, HA auto-discovery, device tracking |
| `main/display.c` | SSD1306 OLED, room selector UI |
| `main/settings.c` | NVS AES-256-GCM encrypted credentials |
| `main/button.c` | BOOT button PTT, WS2812 LED states |
| `main/webserver.c` | HTTP config portal, OTA updates |
| `main/protocol.h` | Shared constants — sample rate, frame size, ports, packet format |

## Hardware Per Node
- ESP32-S3-DevKitC-1 (N8 or N8R2/N8R8 with PSRAM)
- INMP441 mic: I2S Bus 0 — SCK=4, WS=5, SD=6
- MAX98357A amp: I2S Bus 1 — SCK=15, WS=16, SD=17
- SSD1306 OLED 128x64: I2C — SDA=8, SCL=9, addr=0x3C
- WS2812 RGB LED on BOOT button GPIO
- Cycle button GPIO 10 — short=next room, long=call
