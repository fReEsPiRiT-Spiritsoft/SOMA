# brain_core/discovery/__init__.py
"""
SOMA-AI Hardware Discovery — Phase 6
Zero-Config Onboarding via MQTT, mDNS und Home Assistant.

Exports:
  - MQTTListener: MQTT Hello-Packets und Audio-Streams
  - MDNSScanner: Zeroconf Service-Discovery
  - HomeAssistantBridge: HA REST API Sync
  - DiscoveryOrchestrator: Koordination aller Quellen → Unified Registry
"""

from brain_core.discovery.mqtt_listener import MQTTListener
from brain_core.discovery.mDNS_scanner import MDNSScanner
from brain_core.discovery.ha_bridge import HomeAssistantBridge
from brain_core.discovery.orchestrator import DiscoveryOrchestrator

__all__ = [
    "MQTTListener",
    "MDNSScanner",
    "HomeAssistantBridge",
    "DiscoveryOrchestrator",
]
