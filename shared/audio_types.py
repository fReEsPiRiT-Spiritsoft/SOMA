"""
SOMA-AI Audio Protocol Types
==============================
Definitionen für Audio-Metadaten, Patchbay-Routing und Spatial-Awareness.

Phase 6 Erweiterung:
  - RSSIReading: Bluetooth/WiFi Signalstärke pro Raum
  - RoomProbability: Wahrscheinlichkeitsvektor (Nutzer → Raum)
  - SessionInfo: Konversations-Session die mit Nutzer wandert
  - DiscoveredDevice: Einheitliches Gerätemodell aller Discovery-Quellen
  - DeviceStatus: Online/Offline/Unknown Tracking
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


class DeviceStatus(str, Enum):
    """Online/Offline Status für entdeckte Hardware."""
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
    INITIALIZING = "initializing"


class DetectionMethod(str, Enum):
    """Wie ein Nutzer lokalisiert wurde."""
    AUDIO_AMPLITUDE = "audio_amplitude"
    RSSI = "rssi"
    FUSED = "fused"         # Amplitude + RSSI kombiniert
    MANUAL = "manual"       # Manuell gesetzt


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


class RSSIReading(BaseModel):
    """
    RSSI-Messung von einem BLE-Beacon oder WiFi-AP in einem Raum.
    Mehrere Räume melden gleichzeitig → Triangulation.
    """
    device_id: str          # BLE-MAC oder WiFi-BSSID des Nutzers
    room_id: str            # Raum in dem der Scanner steht
    scanner_node_id: str    # Node-ID des Scanners
    rssi_dbm: float         # Signalstärke in dBm (negativ, -30 = nah, -90 = fern)
    frequency_mhz: int = 2400  # 2.4GHz BLE default
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RoomProbability(BaseModel):
    """
    Wahrscheinlichkeit dass ein Nutzer sich in einem bestimmten Raum befindet.
    Bestandteil des RoomProbabilityVector.
    """
    room_id: str
    probability: float = Field(ge=0.0, le=1.0)
    audio_confidence: float = 0.0   # Anteil Audio-Amplitude
    rssi_confidence: float = 0.0    # Anteil RSSI
    last_signal: datetime = Field(default_factory=datetime.utcnow)


class RoomProbabilityVector(BaseModel):
    """
    Vollständiger Wahrscheinlichkeitsvektor: Nutzer → alle Räume.
    Summe aller probabilities ≈ 1.0.
    """
    user_id: str
    rooms: list[RoomProbability] = Field(default_factory=list)
    best_room: Optional[str] = None
    best_confidence: float = 0.0
    detection_method: DetectionMethod = DetectionMethod.AUDIO_AMPLITUDE
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_confident(self) -> bool:
        """True wenn bester Raum > 60% Wahrscheinlichkeit."""
        return self.best_confidence > 0.6


class SessionInfo(BaseModel):
    """
    Konversations-Session die mit dem Nutzer durch Räume wandert.
    Enthält den Kontext der aktuellen Interaktion.
    """
    session_id: str
    user_id: str
    current_room: str
    previous_rooms: list[str] = Field(default_factory=list)
    conversation_context: str = ""      # Letzte N Turns als Kontext
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
    turn_count: int = 0
    is_active: bool = True

    def migrate_to_room(self, new_room: str) -> None:
        """Session wandert in einen neuen Raum."""
        if self.current_room != new_room:
            self.previous_rooms.append(self.current_room)
            self.current_room = new_room
            self.last_activity = datetime.utcnow()


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
    detection_method: str = "audio_amplitude"  # audio_amplitude | rssi | fused | manual
    session_id: Optional[str] = None   # Phase 6: Session die mitwandert
    probability_vector: Optional[RoomProbabilityVector] = None  # Phase 6: Volles Bild
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


class DiscoveredDevice(BaseModel):
    """
    Einheitliches Gerätemodell — egal ob via MQTT, mDNS oder HA entdeckt.
    Die SSOT für alle Hardware im SOMA-Netzwerk.
    """
    device_id: str                          # Eindeutige ID
    name: str                               # Menschenlesbarer Name
    device_type: NodeType = NodeType.MIC    # mic, spk, tab
    protocol: ProtocolType = ProtocolType.MQTT
    status: DeviceStatus = DeviceStatus.UNKNOWN
    room_id: Optional[str] = None           # Zugewiesener Raum
    ip_address: Optional[str] = None
    port: Optional[int] = None
    capabilities: list[str] = Field(default_factory=list)
    firmware_version: Optional[str] = None
    properties: dict[str, str] = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_online(self) -> bool:
        return self.status == DeviceStatus.ONLINE
