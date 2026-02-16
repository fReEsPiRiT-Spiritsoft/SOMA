"""
SOMA-AI Health & System Schemas
================================
Pydantic-Modelle für System-Status, Last-Metriken und Service-Health.
Gemeinsam genutzt von brain_core (Producer) und brain_memory_ui (Consumer/Dashboard).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SystemLoadLevel(str, Enum):
    """Systemlast-Stufen für Model-Routing."""
    IDLE = "idle"            # < 30% – volle Power
    NORMAL = "normal"        # 30-60% – Standard
    ELEVATED = "elevated"    # 60-75% – Light-Model bevorzugen
    HIGH = "high"            # 75-85% – NanoEngine only
    CRITICAL = "critical"    # > 85% – Deferred Reasoning aktiv


class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class GpuMetrics(BaseModel):
    gpu_id: int = 0
    name: str = "unknown"
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    vram_percent: float = 0.0
    gpu_temp_celsius: float = 0.0
    gpu_utilization_percent: float = 0.0


class SystemMetrics(BaseModel):
    """Snapshot der aktuellen Systemlast."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cpu_percent: float = 0.0
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_percent: float = 0.0
    gpu: Optional[GpuMetrics] = None
    cpu_temp_celsius: Optional[float] = None
    load_level: SystemLoadLevel = SystemLoadLevel.IDLE


class ServiceHealth(BaseModel):
    """Health-Status eines einzelnen Services."""
    name: str
    status: ServiceStatus = ServiceStatus.UNAVAILABLE
    latency_ms: Optional[float] = None
    last_check: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None
    circuit_state: Optional[str] = None


class SystemHealthReport(BaseModel):
    """Gesamtbericht: Metriken + Service-Status."""
    metrics: SystemMetrics
    services: dict[str, ServiceHealth] = {}
    active_model: Optional[str] = None
    queued_requests: int = 0
    active_sessions: int = 0


class DeferredRequest(BaseModel):
    """Request der in die Redis-Queue verschoben wird."""
    request_id: str
    user_id: Optional[str] = None
    room_id: Optional[str] = None
    prompt: str
    priority: int = Field(default=5, ge=1, le=10)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    estimated_tokens: int = 0
    metadata: dict = Field(default_factory=dict)
