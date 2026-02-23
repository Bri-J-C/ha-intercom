# Intercom Hub - Home Assistant Add-on

A Home Assistant add-on that acts as the central hub for the HA Intercom system — coordinating MQTT discovery, audio routing, Web PTT clients, TTS announcements, chime management, and call notifications.

## Features

- **Web PTT** — Browser-based push-to-talk via WebSocket (accessible through HA ingress)
- **Per-client tracking** — Each web client gets a unique ID with independent state
- **Audio routing** — ESP32↔ESP32, ESP32↔Web, Web↔Web, TTS→all
- **Priority routing** — Normal, High, Emergency levels with preemption; trail-out silence uses active PTT priority
- **DND awareness** — Respects Do Not Disturb mode, allows emergency override
- **Hub-managed chimes** — Streams WAV chime audio via UDP/Opus when a call notification is received; ESP32 nodes detect the stream and skip the fallback beep
- **Chime management** — Upload custom WAV chimes, select the active chime, and delete chimes via the web UI; chimes persist across add-on rebuilds in `/data/chimes/`
- **All Rooms chime via single multicast** — One UDP/multicast stream reaches all nodes simultaneously; eliminates the per-device race condition that caused missed chimes
- **Chime field in MQTT call payload** — Hub includes the selected chime name in `intercom/call` messages so ESP32 nodes can log which chime is expected
- **Mobile notifications for All Rooms calls** — When an ESP32 initiates an All Rooms call, push notifications are sent to all configured mobile devices
- **Call notifications** — Ring/chime between all node types (ESP32, web, mobile)
- **Mobile device support** — Auto-discovery from HA companion apps
- **TTS announcements** — Via Piper text-to-speech with channel-busy waiting
- **MQTT auto-discovery** — Devices appear automatically in Home Assistant
- **Lovelace PTT card** — Custom card for HA dashboards (`intercom-ptt-card.js` v1.2.0) with ingress and direct WebSocket modes
- **INADDR_ANY multicast socket** — Correctly binds to the LAN interface in Docker/HA add-on environments (`host_network: true` required)
- **Multicast group `239.255.0.100`** (organization-local scope; must match firmware)
- **MulticastMetrics tracking** — TX/RX packet counts, sequence gaps, and duplicates logged for diagnostics
- **Thread-safe state** — Concurrent client handling with proper locking

## Installation

### Option 1: Local Add-on (Development)

1. Copy the `intercom_hub` folder to `/addons/intercom_hub/` on your HA instance
2. Go to **Settings → Add-ons → Add-on Store**
3. Click the three dots menu → **Check for updates**
4. Find "Intercom Hub" under **Local add-ons**
5. Click **Install**

### Option 2: GitHub Repository

1. Go to **Settings → Add-ons → Add-on Store**
2. Click three dots → **Repositories**
3. Add: `https://github.com/Bri-J-C/ha-intercom`
4. Find and install "Intercom Hub"

## Configuration

```yaml
mqtt_host: core-mosquitto      # MQTT broker hostname
mqtt_port: 1883                # MQTT broker port
mqtt_user: "homeassistant"     # MQTT username (required)
mqtt_password: "your_password" # MQTT password (required)
device_name: "Intercom Hub"    # Display name in HA
multicast_group: "239.255.0.100" # Must match ESP32 firmware
multicast_port: 5005           # Must match ESP32 firmware
piper_host: "core-piper"      # Piper TTS addon hostname
piper_port: 10200              # Piper TTS addon port
log_level: "info"              # debug, info, warning, error
mobile_devices:                # Optional: mobile devices for notifications
  - name: "Phone"
    notify_service: "notify.mobile_app_phone"
```

## Usage

### Web PTT (Browser)

Access via the **Intercom** panel in Home Assistant's sidebar. The add-on provides an ingress-based web interface with:
- Push-to-talk button (hold to talk)
- Room/device selector dropdown
- Call button to ring specific rooms
- Chime management panel (upload WAV, select active chime, delete)
- Connection status and TX/RX state indicators
- Device name input on first launch
- AudioContext `nextPlayTime` reset on `suspend()`/`resume()` to prevent stale scheduling after the browser pauses and resumes the audio context

### Chime Management

Chimes are WAV files stored persistently in `/data/chimes/` on the HA host and pre-encoded to Opus frames in memory at startup. Bundled default chimes are seeded to `/data/chimes/` on first run.

**Via the web UI:**
1. Open the Intercom panel in the HA sidebar
2. Expand the **Chime Settings** section
3. Use the dropdown to select the active chime, or click **Preview** to hear it
4. Drag-and-drop (or click) the upload zone to add a new WAV file (max 5MB)
5. Click the delete button next to any non-default chime to remove it

**Via HTTP API (direct):**
- `GET /api/chimes` — list all available chimes with name, frame count, and duration
- `POST /api/chimes/upload` — upload a WAV file as a new chime (multipart/form-data, field name `file`)
- `DELETE /api/chimes/{name}` — delete a chime by name (the `doorbell` chime cannot be deleted)

**Chime name rules:** filename stem, lowercase, alphanumeric characters, dashes, and underscores only (e.g. `my-chime.wav` becomes chime name `my-chime`).

### TTS Announcements

```yaml
action: notify.intercom_hub
data:
  message: "Dinner is ready!"
```

### Broadcast to Specific Room

```yaml
action: notify.intercom_hub
data:
  message: "Come to the kitchen!"
  target: "living_room"  # Optional: specific room or "all" for broadcast
```

### Lovelace PTT Card

Add the custom Lovelace card (v1.2.0) for dashboard integration:

1. Copy `intercom-ptt-card.js` to your HA `www/` directory
2. Add as a resource in **Settings → Dashboards → Resources**:
   - URL: `/local/intercom-ptt-card.js`
   - Type: JavaScript Module
3. Add to your dashboard:
   ```yaml
   type: custom:intercom-ptt-card
   ```

The card connects to the hub via HA ingress by default (no extra configuration needed when accessed through the HA frontend). For direct LAN access outside of HA, add the optional `hub_url` option:

```yaml
type: custom:intercom-ptt-card
hub_url: "ws://<ha-ip>:8099/ws"   # Optional: bypass ingress, connect directly
```

**v1.2.0 changes:** Fixed ingress WebSocket connection to use the add-on's stable ingress entry path (from `/addons/{slug}/info`) and set the ingress session cookie with the correct format matching the HA frontend (including the conditional `Secure` flag for HTTPS). Added `hub_url` direct connection option, inline SVG logo on the init overlay, centered header title via CSS grid, and card version indicator on the init overlay.

### Automations

```yaml
alias: Doorbell Announcement
description: "Announce when someone rings the doorbell"
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.doorbell
    to: "on"
conditions: []
actions:
  - action: notify.intercom_hub
    data:
      message: "Someone is at the door"
mode: single
```

## Entities Created

| Entity | Type | Description |
|--------|------|-------------|
| `notify.intercom_hub` | Notify | Send TTS announcements |
| `sensor.intercom_hub_state` | Sensor | idle/transmitting/receiving |
| `number.intercom_hub_volume` | Number | Volume 0-100% |
| `switch.intercom_hub_mute` | Switch | Mute toggle |
| `select.intercom_hub_target` | Select | Target room selector |
| `switch.intercom_hub_agc` | Switch | Automatic Gain Control toggle |
| `select.intercom_hub_priority` | Select | Priority level (Normal/High/Emergency) |
| `switch.intercom_hub_dnd` | Switch | Do Not Disturb toggle |
| `button.intercom_hub_call` | Button | Send call/ring notification |
| `select.intercom_hub_chime` | Select | Active chime selector |

## Requirements

- Home Assistant with MQTT integration
- ESP32 intercoms on the same network subnet
- Mosquitto broker (or compatible MQTT broker)
- Piper add-on (optional, for TTS)

## Technical Details

- Uses UDP multicast (`239.255.0.100:5005`) to broadcast audio and chime streams
- Multicast socket bound with `INADDR_ANY`; `host_network: true` in add-on config is required for multicast to reach the LAN
- Audio encoded as Opus at 16kHz mono, 32kbps VBR (matches ESP32 firmware)
- WebSocket server for browser PTT clients (binary PCM + JSON control)
- Individual client IDs prevent state cross-contamination between web clients
- First-to-talk collision avoidance with 500ms timeout
- Chime streaming uses wall-clock scheduling to prevent frame-timing drift
- "All Rooms" calls use a single MQTT message and a single multicast UDP stream so all ESP32 nodes receive the chime at approximately the same time
- MQTT self-echo prevention: outgoing call messages are tagged `"source": "hub"` and ignored on receipt

## Versions

| Component | Version |
|-----------|---------|
| Hub Python (`intercom_hub.py`) | 2.5.1 |
| Hub Add-on (`config.yaml`) | 2.1.0 |
| Lovelace PTT Card (`intercom-ptt-card.js`) | 1.2.0 |
| Firmware (`protocol.h`) | 2.9.1 |
