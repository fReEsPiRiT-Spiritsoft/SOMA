"""
SOMA-AI User Models
====================
Profile, Voice-Hashes und Safety-Levels.
Biometrische Daten bleiben lokal (Privacy Vault).
"""

from django.db import models
from django.contrib.auth.models import AbstractUser


class SomaUser(AbstractUser):
    """
    Erweitertes User-Model mit SOMA-spezifischen Feldern.
    """

    class SafetyLevel(models.TextChoices):
        CHILD = "child", "Kind (< 12)"
        TEEN = "teen", "Jugendlich (12-18)"
        ADULT = "adult", "Erwachsen"
        ADMIN = "admin", "Administrator"

    class Meta:
        verbose_name = "SOMA Benutzer"
        verbose_name_plural = "SOMA Benutzer"

    safety_level = models.CharField(
        max_length=5,
        choices=SafetyLevel.choices,
        default=SafetyLevel.ADULT,
        help_text="Sicherheitsstufe für Content-Filtering",
    )
    is_voice_enrolled = models.BooleanField(
        default=False,
        help_text="Stimme ist registriert für Erkennung",
    )
    preferred_language = models.CharField(
        max_length=10,
        default="de",
        help_text="Bevorzugte Sprache (de, en, ...)",
    )
    preferred_room = models.ForeignKey(
        "hardware.Room",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Standard-Raum des Nutzers",
    )
    persona_notes = models.TextField(
        blank=True,
        default="",
        help_text="Was SOMA über diesen Nutzer gelernt hat (Ambient Learning)",
    )

    def __str__(self):
        return f"{self.username} ({self.get_safety_level_display()})"


class VoiceProfile(models.Model):
    """
    Stimmprofil für Speaker-Erkennung.
    Voice-Hash statt Rohdaten (Privacy!).
    """

    class Meta:
        verbose_name = "Stimmprofil"
        verbose_name_plural = "Stimmprofile"

    user = models.OneToOneField(
        SomaUser,
        on_delete=models.CASCADE,
        related_name="voice_profile",
    )
    voice_hash = models.CharField(
        max_length=512,
        unique=True,
        help_text="Kryptographischer Hash des Stimm-Embeddings",
    )
    pitch_mean_hz = models.FloatField(
        default=0.0,
        help_text="Durchschnittliche Grundfrequenz (Hz)",
    )
    pitch_std_hz = models.FloatField(
        default=0.0,
        help_text="Standardabweichung der Grundfrequenz (Hz)",
    )
    enrollment_samples = models.IntegerField(
        default=0,
        help_text="Anzahl der Enrollment-Samples",
    )
    last_verified = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Letzte erfolgreiche Verifizierung",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Voice: {self.user.username} ({self.pitch_mean_hz:.0f} Hz)"


class InteractionLog(models.Model):
    """
    Anonymisiertes Interaktionslog für Ambient Learning.
    Keine Prompts gespeichert – nur Metadaten!
    """

    class Meta:
        verbose_name = "Interaktionslog"
        verbose_name_plural = "Interaktionslogs"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["user", "-timestamp"]),
        ]

    user = models.ForeignKey(
        SomaUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interactions",
    )
    room = models.ForeignKey(
        "hardware.Room",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    engine_used = models.CharField(max_length=20, default="unknown")
    was_deferred = models.BooleanField(default=False)
    latency_ms = models.FloatField(null=True, blank=True)
    intent_category = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Kategorie (smalltalk, device_control, question, ...)",
    )
    mood_detected = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Erkannte Stimmung (neutral, happy, stressed, ...)",
    )
    satisfaction_signal = models.FloatField(
        null=True,
        blank=True,
        help_text="0-1 Zufriedenheitssignal (aus Folge-Interaktion abgeleitet)",
    )

    def __str__(self):
        user = self.user.username if self.user else "anonym"
        return f"{self.timestamp:%Y-%m-%d %H:%M} | {user} | {self.engine_used}"
