# QA Test Report -- 2026-02-26 21:47

Devices: Bedroom v2.9.2 (10.0.0.15), INTERCOM2 v2.9.2 (10.0.0.14), Hub (10.0.0.8:8099)
Log capture: serial=0/2, hub=off, mqtt=off

## Summary

| Status | Count |
|--------|-------|
| PASS   | 10 |
| FAIL   | 0 |
| SKIP   | 0 |
| Stale audio warnings | 0 |
| Reboots detected | 0 |
| Crash patterns found | 0 |
| **Total** | **10** |

## Results

| ID | Name | Status | Detail |
|----|------|--------|--------|
| S01 | Device A sustained_tx -> Device B receives (multicast) | PASS | INTERCOM2 rx_delta=972, hub_pkts=972, seq=OK(972/243) |
| S02 | Device B sustained_tx -> Device A receives (multicast) | PASS | Bedroom rx_delta=247, hub_pkts=247, seq=OK(247/247) |
| S03 | Device A unicast -> only Device B receives (isolation) | PASS | INTERCOM2 rx_delta=245, Bedroom rx_delta=0 (isolation ok) |
| S04 | Hub chime -> specific device (unicast) | PASS | INTERCOM2 rx_delta=150 (chime delivered) |
| S05 | Hub chime -> All Rooms (multicast) | PASS | Bedroom rx_delta=155, INTERCOM2 rx_delta=214 |
| S06 | Web PTT -> specific device | PASS | INTERCOM2 rx_delta=195, Bedroom rx_delta=0 |
| S07 | Web PTT -> All Rooms | PASS | Bedroom rx_delta=195, INTERCOM2 rx_delta=195 |
| S08 | TTS -> device (SKIP if Piper unavailable) | PASS | Bedroom rx_delta=133 (TTS delivered) |
| S09 | Device sustained_tx -> hub audio_stats | PASS | Hub received 944 packets from Bedroom, seq=OK(944/236) |
| S10 | QAudioSender 440Hz -> hub content verify | PASS | Hub received 150 QA packets, Bedroom rx_delta=1, seq=OK(150/150) |
