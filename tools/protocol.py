"""
HA Intercom Protocol Constants

Shared protocol definitions for the Home Assistant Intercom system.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# Network Configuration
CONTROL_PORT = 5004      # Discovery and config
AUDIO_PORT = 5005        # Audio streaming
MULTICAST_GROUP = "224.0.0.100"
MULTICAST_TTL = 1        # Local network only

# Audio Configuration
SAMPLE_RATE = 16000      # 16kHz
CHANNELS = 1             # Mono
FRAME_DURATION_MS = 20   # 20ms frames
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 320 samples
OPUS_BITRATE = 24000     # 24kbps for voice

# Protocol Configuration
HEARTBEAT_INTERVAL = 30  # seconds
DEVICE_ID_LENGTH = 8     # bytes
SEQUENCE_LENGTH = 4      # bytes
HEADER_LENGTH = DEVICE_ID_LENGTH + SEQUENCE_LENGTH  # 12 bytes


class MessageType(IntEnum):
    """Control message types."""
    ANNOUNCE = 1
    CONFIG = 2
    PING = 3
    PONG = 4


class CastType(IntEnum):
    """Audio cast types (inspired by PTTDroid)."""
    UNICAST = 0
    MULTICAST = 1
    BROADCAST = 2  # Same as multicast for our purposes


@dataclass
class AudioPacket:
    """Audio packet structure."""
    device_id: bytes      # 8 bytes
    sequence: int         # uint32
    opus_data: bytes      # Variable length

    def pack(self) -> bytes:
        """Pack packet for transmission."""
        return self.device_id + struct.pack(">I", self.sequence) + self.opus_data

    @classmethod
    def unpack(cls, data: bytes) -> "AudioPacket":
        """Unpack received packet."""
        device_id = data[:DEVICE_ID_LENGTH]
        sequence = struct.unpack(">I", data[DEVICE_ID_LENGTH:HEADER_LENGTH])[0]
        opus_data = data[HEADER_LENGTH:]
        return cls(device_id=device_id, sequence=sequence, opus_data=opus_data)


@dataclass
class AnnounceMessage:
    """Device announcement message."""
    device_id: str
    name: str
    ip: str
    version: str = "1.0.0"
    capabilities: list = None

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = ["audio", "ptt"]

    def to_dict(self) -> dict:
        return {
            "type": "announce",
            "device_id": self.device_id,
            "name": self.name,
            "ip": self.ip,
            "version": self.version,
            "capabilities": self.capabilities,
        }


@dataclass
class ConfigMessage:
    """Configuration message from HA to device."""
    device_id: str
    room: str
    default_target: str
    volume: int = 80
    muted: bool = False
    targets: dict = None

    def __post_init__(self):
        if self.targets is None:
            self.targets = {"all": MULTICAST_GROUP}

    def to_dict(self) -> dict:
        return {
            "type": "config",
            "device_id": self.device_id,
            "room": self.room,
            "default_target": self.default_target,
            "volume": self.volume,
            "muted": self.muted,
            "targets": self.targets,
        }


def generate_device_id(name: str = "python") -> bytes:
    """Generate an 8-byte device ID."""
    import hashlib
    import uuid
    unique = f"{name}_{uuid.getnode()}"
    return hashlib.sha256(unique.encode()).digest()[:DEVICE_ID_LENGTH]
