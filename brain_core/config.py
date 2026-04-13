"""
SOMA-AI Brain Core Configuration
==================================
Zentrale Konfiguration mit Environment-Variablen und Hardware-Limits.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class SomaConfig(BaseSettings):
    """System-wide configuration loaded from .env"""

    # ── Server ───────────────────────────────────────────────────────────
    brain_core_host: str = "0.0.0.0"
    brain_core_port: int = 8100
    brain_core_workers: int = 4

    # ── PostgreSQL ───────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "soma_db"
    postgres_user: str = "soma"
    postgres_password: str = "soma_secret_change_me"

    # ── Redis ────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""  # Empty = no auth for local dev

    # ── MQTT ─────────────────────────────────────────────────────────────
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_ws_port: int = 9001

    # ── Ollama ───────────────────────────────────────────────────────────
    ollama_host: str = "http://localhost"
    ollama_port: int = 11434
    ollama_heavy_model: str = "gemma4:e4b"        # Oracle: Gemma4 E4B ≈ 9.6GB VRAM (128K ctx, Vision, Thinking)
    ollama_light_model: str = "qwen3:1.7b"       # Draft: 1.7B Q4 ≈ 1.2GB VRAM
    ollama_num_parallel: int = 2
    ollama_max_loaded_models: int = 3              # Oracle + Draft + Whisper Headroom
    # Warm Pool: Wie lange Modelle im VRAM bleiben (Ollama keep_alive)
    # Vision #2: Laenger im VRAM halten = Prefix-Cache bleibt warm
    # Ollama cached den System-Prompt (Persona) als KV-Prefix automatisch
    # wenn das Modell geladen bleibt. 30m statt 5m = deutlich schnellerer TTFT.
    ollama_heavy_keep_alive: str = "30m"          # Oracle: 30min idle → unload
    ollama_light_keep_alive: str = "-1s"           # Draft: PERMANENT im VRAM (nur ~1.2GB)

    # ── Speculative Decoding ─────────────────────────────────────────────
    speculative_enabled: bool = True               # Application-Level Spec. Decoding
    speculative_draft_batch: int = 10              # Draft-Tokens pro Runde

    # ── KV-Cache Strategie (Phase E) ─────────────────────────────────────
    session_stale_trim_turns: int = 6              # Stale Sessions: Letzte N Turns behalten
    session_max_idle_secs: float = 300.0           # 5min idle → Session als stale markieren

    # ── GitHub Models API (Code-Generierung für Plugins) ────────────────
    # PAT mit 'models:read' Scope: https://github.com/settings/tokens
    github_token: str = ""
    # Modell für Plugin-Code-Generierung (o1-mini, o1-preview, gpt-4o, o4-mini)
    github_models_model: str = "o4-mini"

    # ── Health Thresholds ────────────────────────────────────────────────
    health_ram_warn_percent: float = 85.0      # War 75%, zu niedrig mit Ollama
    health_ram_critical_percent: float = 92.0   # War 85%
    health_vram_warn_percent: float = 75.0
    health_vram_critical_percent: float = 97.0
    health_cpu_warn_percent: float = 80.0
    health_temp_warn_celsius: float = 75.0
    # ── VRAM Management ─────────────────────────────────────────────
    # Ollama VRAM freigeben nach N Sekunden ohne LLM-Request (0 = deaktiviert)
    # Phase B: 120s statt 10s — Modell bleibt warm für schnelle Streaming-Responses
    vram_unload_idle_secs: float = 120.0
    # Heavy-Engine aktiv lassen bis VRAM diese Auslastung überschreitet
    # Gemma4 E4B nutzt ~95% VRAM auf RTX 3060 (12GB) → 98% damit Modell geladen bleibt
    heavy_engine_max_vram_pct: float = 98.0
    # ── Home Assistant ───────────────────────────────────────────────────
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    ha_speaker_entity: str = "media_player.all"  # HA entity for home broadcasts

    # ── Phone Gateway (Asterisk ARI) ─────────────────────────────────────
    # See asterisk/ directory for setup instructions.
    # docker compose up asterisk to start the gateway.
    asterisk_host: str = "localhost"
    asterisk_ari_port: int = 8088
    asterisk_ari_user: str = "soma-ari"
    asterisk_ari_pass: str = "soma_ari_secret"   # CHANGE THIS in .env!
    phone_recordings_dir: str = "data/phone_recordings"
    phone_sounds_dir: str = "data/phone_sounds"
    # Password spoken over phone to authenticate (CHANGE THIS!)
    soma_phone_password: str = "starlight"
    # Optional: SHA-256 hash of password (more secure). If set, overrides soma_phone_password.
    soma_phone_password_hash: str = ""
    # SOMA's reachable URL for Home Assistant to fetch TTS audio
    soma_local_url: str = "http://192.168.0.100:8100"  # CHANGE to your machine's LAN IP

    # ── Sudo Mode ────────────────────────────────────────────────────────
    # If True at boot, SOMA starts with sudo enabled (can be toggled via API)
    sudo_mode_enabled: bool = False

    # ── Django SSOT ──────────────────────────────────────────────────────
    django_host: str = "0.0.0.0"
    django_port: int = 8200
    django_secret_key: str = "soma-django-secret-change-me-in-production"
    django_debug: bool = True

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def ollama_url(self) -> str:
        return f"{self.ollama_host}:{self.ollama_port}"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def django_api_base(self) -> str:
        return f"http://{self.django_host}:{self.django_port}/api"

    class Config:
        env_file = ".env"
        extra = "ignore"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton
settings = SomaConfig()


# ═══════════════════════════════════════════════════════════════════
#  RUNTIME SUDO TOGGLE
# ═══════════════════════════════════════════════════════════════════
# Can be changed at runtime via /api/sudo endpoint.
# Separate from config file — doesn't persist across restarts
# unless sudo_mode_enabled=True is set in .env.

_sudo_mode: bool = settings.sudo_mode_enabled


def is_sudo_enabled() -> bool:
    """Check if sudo mode is currently active."""
    return _sudo_mode


def set_sudo_mode(enabled: bool) -> None:
    """Toggle sudo mode at runtime (via dashboard API)."""
    global _sudo_mode
    _sudo_mode = enabled
