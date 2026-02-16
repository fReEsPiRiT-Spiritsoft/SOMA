"""
SOMA-AI Audio Protocol Types
==============================
Definitionen für Audio-Metadaten, Patchbay-Routing und Spatial-Awareness.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    MIC = "mic"
    SPK = "spk"
    TAB = "tab"  # Tablet (hat beides)


class ProtocolType(str, Enum):
    MQTT = "mqtt"
    HA = "ha"       # Home Assistant
    MDNS = "mdns"


class AudioChunkMeta(BaseModel):
    """Metadaten eines Audio-Chunks vom Mikrofon-Node."""
    node_id: str
    room_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sample_rate: int = 16000
    channels: int = 1
    amplitude_rms: float = 0.0
    is_speech: bool = False
    speaker_embedding: Optional[list[float]] = None


class PatchRoute(BaseModel):
    """Eine aktive Verbindung: Mikrofon → Lautsprecher(n)."""
    route_id: str
    source_node_id: str  # Mic
    target_node_ids: list[str]  # Speaker(s)
    room_id: str
    session_id: Optional[str] = None
    priority: int = Field(default=5, ge=1, le=10)
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PresenceEvent(BaseModel):
    """Presence-Change Event für Raum-Wanderung."""
    user_id: str
    from_room: Optional[str] = None
    to_room: str
    confidence: float = Field(ge=0.0, le=1.0)
    detection_method: str = "audio_amplitude"  # audio_amplitude | rssi | manual
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SpeakerProfile(BaseModel):
    """Stimmprofil für Speaker-Erkennung."""
    user_id: str
    voice_hash: str
    pitch_mean_hz: float = 0.0
    pitch_std_hz: float = 0.0
    is_child: bool = False
    confidence: float = 0.0
    last_seen: datetime = Field(default_factory=datetime.utcnow)


class HardwareHello(BaseModel):
    """MQTT Hello-Paket von neuer Hardware."""
    node_id: str
    node_type: NodeType
    protocol: ProtocolType
    capabilities: list[str] = Field(default_factory=list)
    firmware_version: Optional[str] = None
    ip_address: Optional[str] = None
    mqtt_topic: Optional[str] = None
