"""
SOMA-AI Hardware Models (SSOT)
===============================
DIE EINZIGE WAHRHEIT über die physische Welt.
brain_core bezieht ALLES Wissen über Räume und Hardware von hier.

Models:
  Room ──────────── Physischer Raum (is_kids_room Flag!)
    │
    └── HardwareNode ── Physisches Gerät (MIC / SPK / TAB)
          │
          └── NodeCapability ── Was kann das Gerät? (I/O)

Datenfluss:
  Discovery (MQTT/mDNS/HA) ──► Django REST API ──► HardwareNode erstellen
  brain_core.audio_router  ──► Django REST API ──► Hardware-Daten abrufen
  Admin Dashboard          ──► Django Admin    ──► Manuelle Verwaltung
"""

from django.db import models
from django.utils import timezone


class Room(models.Model):
    """
    Physischer Raum im Smart Home.
    SSOT für räumliche Zuordnung aller Hardware-Nodes.
    """

    class Meta:
        verbose_name = "Raum"
        verbose_name_plural = "Räume"
        ordering = ["name"]

    id = models.AutoField(primary_key=True)
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Eindeutiger Raumname (z.B. 'Wohnzimmer')",
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="URL-sicherer Bezeichner (z.B. 'wohnzimmer')",
    )
    floor = models.IntegerField(
        default=0,
        help_text="Stockwerk (0 = EG, 1 = OG, -1 = UG)",
    )
    is_kids_room = models.BooleanField(
        default=False,
        help_text="Kinderzimmer? Aktiviert Child-Safe Mode automatisch.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Raum ist aktiv und wird überwacht.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optionale Beschreibung / Notizen.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        suffix = " 👶" if self.is_kids_room else ""
        return f"{self.name} (F{self.floor}){suffix}"

    @property
    def node_count(self) -> int:
        return self.hardware_nodes.count()


class HardwareNode(models.Model):
    """
    Physisches Gerät im SOMA-Netzwerk.
    Jedes Mikrofon, jeder Lautsprecher, jedes Tablet ist ein Node.
    """

    class NodeType(models.TextChoices):
        MIC = "mic", "Mikrofon"
        SPK = "spk", "Lautsprecher"
        TAB = "tab", "Tablet"
        SEN = "sen", "Sensor"
        ACT = "act", "Aktor"

    class Protocol(models.TextChoices):
        MQTT = "mqtt", "MQTT"
        HA = "ha", "Home Assistant"
        MDNS = "mdns", "mDNS / Zeroconf"
        HTTP = "http", "HTTP Direct"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        DEGRADED = "degraded", "Eingeschränkt"
        UNKNOWN = "unknown", "Unbekannt"

    class Meta:
        verbose_name = "Hardware-Node"
        verbose_name_plural = "Hardware-Nodes"
        ordering = ["room", "node_type", "name"]
        indexes = [
            models.Index(fields=["node_id"]),
            models.Index(fields=["room", "node_type"]),
            models.Index(fields=["status"]),
        ]

    id = models.AutoField(primary_key=True)
    node_id = models.CharField(
        max_length=200,
        unique=True,
        help_text="Eindeutige Hardware-ID (z.B. 'mic_wohnzimmer_01')",
    )
    name = models.CharField(
        max_length=200,
        help_text="Menschenlesbarer Name",
    )
    node_type = models.CharField(
        max_length=3,
        choices=NodeType.choices,
        help_text="Gerätetyp: Mikrofon, Lautsprecher, Tablet, Sensor, Aktor",
    )
    protocol = models.CharField(
        max_length=4,
        choices=Protocol.choices,
        default=Protocol.MQTT,
        help_text="Kommunikationsprotokoll",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hardware_nodes",
        help_text="Zugeordneter Raum",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP-Adresse (bei Netzwerk-Geräten)",
    )
    mqtt_topic = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="MQTT Topic für Kommunikation",
    )
    ha_entity_id = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Home Assistant Entity-ID",
    )
    firmware_version = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Node ist aktiv und soll verwendet werden.",
    )
    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Letzter Lebenszeichens-Zeitpunkt",
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Gerätespezifische Konfiguration (JSON)",
    )
    discovered_via = models.CharField(
        max_length=20,
        blank=True,
        default="manual",
        help_text="Wie wurde das Gerät entdeckt? (mqtt_hello, mdns, ha, manual)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        room_name = self.room.name if self.room else "Unzugeordnet"
        return f"{self.get_node_type_display()} '{self.name}' [{room_name}] ({self.status})"

    def mark_online(self):
        self.status = self.Status.ONLINE
        self.last_seen = timezone.now()
        self.save(update_fields=["status", "last_seen", "updated_at"])

    def mark_offline(self):
        self.status = self.Status.OFFLINE
        self.save(update_fields=["status", "updated_at"])

    def to_api_dict(self) -> dict:
        """Serialisierung für brain_core REST API."""
        return {
            "node_id": self.node_id,
            "name": self.name,
            "node_type": self.node_type,
            "protocol": self.protocol,
            "room_id": self.room.slug if self.room else None,
            "room_name": self.room.name if self.room else None,
            "is_kids_room": self.room.is_kids_room if self.room else False,
            "ip_address": str(self.ip_address) if self.ip_address else None,
            "mqtt_topic": self.mqtt_topic,
            "ha_entity_id": self.ha_entity_id,
            "status": self.status,
            "is_active": self.is_active,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "config": self.config,
        }


class NodeCapability(models.Model):
    """
    Was kann ein Hardware-Node?
    Beispiele: audio_input, audio_output, temperature_sensor, display
    """

    class Direction(models.TextChoices):
        INPUT = "input", "Eingang"
        OUTPUT = "output", "Ausgang"
        BIDIRECTIONAL = "bidi", "Bidirektional"

    class Meta:
        verbose_name = "Node-Fähigkeit"
        verbose_name_plural = "Node-Fähigkeiten"
        unique_together = ["node", "capability"]

    id = models.AutoField(primary_key=True)
    node = models.ForeignKey(
        HardwareNode,
        on_delete=models.CASCADE,
        related_name="capabilities",
    )
    capability = models.CharField(
        max_length=100,
        help_text="Fähigkeit (z.B. 'audio_input', 'display', 'temperature')",
    )
    direction = models.CharField(
        max_length=6,
        choices=Direction.choices,
        default=Direction.INPUT,
    )
    parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Capability-spezifische Parameter (z.B. sample_rate, resolution)",
    )

    def __str__(self):
        return f"{self.node.name}: {self.capability} ({self.direction})"
