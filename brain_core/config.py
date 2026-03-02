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
    ollama_heavy_model: str = "llama3:8b"
    ollama_light_model: str = "phi3:mini"
    ollama_num_parallel: int = 2
    ollama_max_loaded_models: int = 2

    # ── Health Thresholds ────────────────────────────────────────────────
    health_ram_warn_percent: float = 75.0
    health_ram_critical_percent: float = 85.0
    health_vram_warn_percent: float = 75.0
    health_vram_critical_percent: float = 85.0
    health_cpu_warn_percent: float = 80.0
    health_temp_warn_celsius: float = 75.0

    # ── Home Assistant ───────────────────────────────────────────────────
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""

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
