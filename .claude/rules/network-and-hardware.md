# Network & Hardware

## Device IPs
| Device | IP | Notes |
|---|---|---|
| Home Assistant server | 10.0.0.8 | MQTT broker, add-on host |
| Bedroom Intercom | 10.0.0.15 | |
| INTERCOM2 | 10.0.0.14 | Weak WiFi — OTA via HA server only |
| Office Intercom | 10.0.0.41 | Was offline |
| Multicast audio | 239.255.0.100:5005 | |

## WS2812 LED States
| Color | State |
|---|---|
| White | Idle |
| Cyan | Transmitting |
| Green | Receiving |
| Red | Muted |
| Orange | Channel busy |
| Purple | Do Not Disturb |

## Key Implementation Constraints
- `IP_MULTICAST_LOOP=0` must be set on TX socket in BOTH hub and firmware — prevents devices receiving their own multicast
- `sdkconfig.esp32s3` controls PSRAM — NOT `sdkconfig.defaults`. After any sdkconfig change: `pio run -t fullclean` before building
- INTERCOM2 (10.0.0.14) cannot be flashed directly — must stage through HA server. Use `flash-intercom2` skill.
- Opus encoder/decoder: ~36KB, allocated in PSRAM. Use `esp_ptr_external_ram()` to verify. `SPIRAM_IGNORE_NOTFOUND=y` ensures boards without PSRAM still boot.

## Settings Encryption (ESP32)
- AES-256-GCM: version byte + 12-byte IV + ciphertext + 16-byte GCM tag
- Key: SHA-256(salt + eFuse MAC) — unique per device
- Encrypted: wifi_pass, mqtt_pass, web_pass, ap_pass
- Backwards compatible: detects plaintext vs encrypted on read

## mDNS Behavior
- Initialized before WiFi connect (catches `IP_EVENT_STA_GOT_IP`)
- Re-enabled with `MDNS_EVENT_ENABLE_IP4` on reconnect
- 60s periodic re-announcement timer as safety net
- DHCP hostname via `esp_netif_set_hostname()`
