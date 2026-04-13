"""
SOMA-AI Action Validator
=========================
Validiert Actions BEVOR sie ausgeführt werden.
Inspiriert von Claude Code's validateInput + checkPermissions Pattern.

Zweistufige Prüfung:
  1. validate()         → Technische Validierung (Schema, Types, Required)
  2. check_permission() → Berechtigungsprüfung (Kind-Modus, Destructive, Rate-Limit)

Wird vom ActionExecutor VOR jeder Action-Ausführung aufgerufen.
"""

from __future__ import annotations

import asyncio
from typing import Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from brain_core.action_registry import get_tag_info, get_all_tags

logger = structlog.get_logger("soma.action_validator")


@dataclass
class ValidationResult:
    """Ergebnis einer Validierung."""
    valid: bool
    error_message: Optional[str] = None
    error_code: Optional[str] = None  # Für strukturiertes Error-Handling
    suggestion: Optional[str] = None  # Hilfreiche Hinweise

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def failure(cls, message: str, code: str = "VALIDATION_ERROR", suggestion: str = None) -> "ValidationResult":
        return cls(valid=False, error_message=message, error_code=code, suggestion=suggestion)


@dataclass
class PermissionResult:
    """Ergebnis einer Berechtigungsprüfung."""
    allowed: bool
    reason: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None
    
    @classmethod
    def allow(cls) -> "PermissionResult":
        return cls(allowed=True)
    
    @classmethod
    def deny(cls, reason: str) -> "PermissionResult":
        return cls(allowed=False, reason=reason)
    
    @classmethod
    def confirm(cls, prompt: str) -> "PermissionResult":
        return cls(allowed=True, requires_confirmation=True, confirmation_prompt=prompt)


@dataclass
class DenialTracking:
    """Tracking von Action-Ablehnungen (wie Claude Code's denialTracking)."""
    denials: dict[str, list[datetime]] = field(default_factory=dict)
    max_denials_per_hour: int = 5
    cooldown_minutes: int = 30
    
    def record_denial(self, tag_type: str) -> None:
        """Ablehnungaufzeichnen."""
        if tag_type not in self.denials:
            self.denials[tag_type] = []
        self.denials[tag_type].append(datetime.now())
        # Alte Einträge bereinigen
        cutoff = datetime.now() - timedelta(hours=1)
        self.denials[tag_type] = [d for d in self.denials[tag_type] if d > cutoff]
    
    def should_auto_deny(self, tag_type: str) -> Tuple[bool, Optional[str]]:
        """Prüft ob wegen zu vieler Ablehnungen automatisch abgelehnt werden soll."""
        if tag_type not in self.denials:
            return False, None
        
        recent_denials = len(self.denials[tag_type])
        if recent_denials >= self.max_denials_per_hour:
            return True, f"Action '{tag_type}' wurde in der letzten Stunde {recent_denials}x abgelehnt"
        return False, None


class ActionValidator:
    """
    Validiert Action-Tags bevor sie ausgeführt werden.
    
    Nutzung:
        validator = ActionValidator()
        
        # Schritt 1: Technische Validierung
        result = await validator.validate("ha_call", {"domain": "light", ...})
        if not result.valid:
            handle_error(result.error_message)
        
        # Schritt 2: Berechtigungsprüfung
        perm = await validator.check_permission("ha_call", params, user_context)
        if not perm.allowed:
            handle_denied(perm.reason)
        if perm.requires_confirmation:
            ask_user(perm.confirmation_prompt)
    """

    def __init__(self):
        self._denial_tracking = DenialTracking()
        self._rate_limits: dict[str, list[datetime]] = {}
        
        # Destructive Actions die besondere Vorsicht brauchen
        self._destructive_actions = {
            "shell_cmd",     # Kann alles tun
            "file_write",    # Schreibt Dateien
            "file_delete",   # Löscht Dateien
            "ha_call",       # Kann Geräte steuern (aber meist harmlos)
        }
        
        # Actions die im Kind-Modus komplett blockiert werden
        self._adult_only_actions = {
            "shell_cmd",
            "browse",  # Kann unsichere Seiten öffnen
        }

    # ══════════════════════════════════════════════════════════════════════
    # STUFE 1: Technische Validierung
    # ══════════════════════════════════════════════════════════════════════

    async def validate(self, tag_type: str, params: dict) -> ValidationResult:
        """
        Technische Validierung eines Action-Tags.
        
        Prüft:
          - Tag existiert
          - Required Parameter vorhanden
          - Parameter-Typen korrekt
          - Werte in erlaubtem Bereich
        """
        info = get_tag_info(tag_type)
        
        # Tag existiert?
        if not info:
            return ValidationResult.failure(
                f"Unbekannter Action-Tag: '{tag_type}'",
                code="UNKNOWN_TAG",
                suggestion=self._suggest_similar_tag(tag_type)
            )
        
        # Required Parameter prüfen
        for param_name, param_spec in info.get("params", {}).items():
            if param_spec.get("required") and param_name not in params:
                return ValidationResult.failure(
                    f"Pflichtparameter '{param_name}' fehlt für {tag_type}",
                    code="MISSING_REQUIRED_PARAM",
                    suggestion=f"Format: [ACTION:{tag_type} {param_name}=\"...\"]"
                )
        
        # Type-Validierung
        for param_name, value in params.items():
            spec = info.get("params", {}).get(param_name, {})
            expected_type = spec.get("type", "string")
            
            type_result = self._validate_param_type(param_name, value, expected_type)
            if not type_result.valid:
                return type_result
        
        # Domain-spezifische Validierung
        validation_method = getattr(self, f"_validate_{tag_type}", None)
        if validation_method:
            return await validation_method(params)
        
        return ValidationResult.success()

    def _validate_param_type(self, name: str, value: str, expected: str) -> ValidationResult:
        """Prüft ob ein Parameter den erwarteten Typ hat."""
        try:
            if expected == "number":
                float(value)  # Kann int oder float sein
            elif expected == "boolean":
                if value.lower() not in ("true", "false", "1", "0"):
                    return ValidationResult.failure(
                        f"Parameter '{name}' muss true/false sein, nicht '{value}'",
                        code="INVALID_TYPE"
                    )
            # string ist immer OK
        except (ValueError, TypeError):
            return ValidationResult.failure(
                f"Parameter '{name}' muss eine Zahl sein, nicht '{value}'",
                code="INVALID_TYPE"
            )
        return ValidationResult.success()

    def _suggest_similar_tag(self, unknown_tag: str) -> Optional[str]:
        """Schlägt ähnliche Tags vor (Levenshtein oder simple Prefix-Match)."""
        all_tags = list(get_all_tags().keys())
        
        # Einfacher Prefix-Match
        for tag in all_tags:
            if tag.startswith(unknown_tag[:3]) or unknown_tag.startswith(tag[:3]):
                return f"Meintest du vielleicht '{tag}'?"
        
        return None

    # ── Domain-spezifische Validierung ──────────────────────────────────────

    async def _validate_ha_call(self, params: dict) -> ValidationResult:
        """Spezielle Validierung für Home Assistant Calls."""
        domain = params.get("domain", "")
        service = params.get("service", "")
        entity_id = params.get("entity_id", "")
        
        # Domain-Service Kombinationen prüfen
        valid_services = {
            "light": ["turn_on", "turn_off", "toggle", "brightness"],
            "switch": ["turn_on", "turn_off", "toggle"],
            "climate": ["set_temperature", "turn_on", "turn_off", "set_hvac_mode"],
            "media_player": ["play_media", "volume_set", "media_play", "media_pause", "media_stop"],
            "cover": ["open_cover", "close_cover", "stop_cover"],
        }
        
        if domain in valid_services and service not in valid_services[domain]:
            return ValidationResult.failure(
                f"Service '{service}' ist für Domain '{domain}' nicht typisch",
                code="UNUSUAL_SERVICE",
                suggestion=f"Bekannte Services für {domain}: {', '.join(valid_services[domain])}"
            )
        
        # Entity-ID Format prüfen
        if entity_id and "." not in entity_id:
            return ValidationResult.failure(
                f"Entity-ID '{entity_id}' hat ungültiges Format (erwartet: domain.name)",
                code="INVALID_ENTITY_FORMAT"
            )
        
        return ValidationResult.success()

    async def _validate_reminder(self, params: dict) -> ValidationResult:
        """Validierung für Timer/Reminder."""
        has_time = any(k in params for k in ["minutes", "seconds", "hours", "time"])
        
        if not has_time:
            return ValidationResult.failure(
                "Reminder braucht eine Zeitangabe (minutes, seconds, hours oder time)",
                code="MISSING_TIME",
                suggestion='[ACTION:reminder minutes="5" topic="..."]'
            )
        
        # Zeit in der Vergangenheit?
        if "time" in params:
            # TODO: Parse time und prüfe ob in Zukunft
            pass
        
        return ValidationResult.success()

    async def _validate_search(self, params: dict) -> ValidationResult:
        """Validierung für Web-Suche."""
        query = params.get("query", "")
        
        if len(query) < 2:
            return ValidationResult.failure(
                "Suchbegriff zu kurz",
                code="QUERY_TOO_SHORT"
            )
        
        if len(query) > 200:
            return ValidationResult.failure(
                "Suchbegriff zu lang (max 200 Zeichen)",
                code="QUERY_TOO_LONG"
            )
        
        return ValidationResult.success()

    # ══════════════════════════════════════════════════════════════════════
    # STUFE 2: Berechtigungsprüfung
    # ══════════════════════════════════════════════════════════════════════

    async def check_permission(
        self,
        tag_type: str,
        params: dict,
        user_context: Optional[dict] = None
    ) -> PermissionResult:
        """
        Prüft ob die Action ausgeführt werden darf.
        
        Berücksichtigt:
          - Kind-Modus (is_child)
          - Destructive Actions
          - Rate-Limiting
          - Denial-Tracking
        """
        user_context = user_context or {}
        info = get_tag_info(tag_type)
        
        if not info:
            return PermissionResult.deny(f"Unbekannte Action: {tag_type}")
        
        # 1. Auto-Deny wegen zu vieler Ablehnungen
        should_deny, reason = self._denial_tracking.should_auto_deny(tag_type)
        if should_deny:
            return PermissionResult.deny(reason)
        
        # 2. Kind-Modus Prüfung
        if user_context.get("is_child", False):
            if tag_type in self._adult_only_actions:
                return PermissionResult.deny(
                    f"'{tag_type}' ist im Kindermodus nicht erlaubt"
                )
        
        # 3. Rate-Limiting
        rate_result = self._check_rate_limit(tag_type)
        if not rate_result.allowed:
            return rate_result
        
        # 4. Destructive Action → Confirmation bei sensiblen Parametern
        if tag_type in self._destructive_actions:
            if self._is_sensitive_operation(tag_type, params):
                return PermissionResult.confirm(
                    f"Diese Aktion ({tag_type}) könnte Auswirkungen haben. Fortfahren?"
                )
        
        # 5. Domain-spezifische Permission-Checks
        perm_method = getattr(self, f"_perm_{tag_type}", None)
        if perm_method:
            return await perm_method(params, user_context)
        
        return PermissionResult.allow()

    def _check_rate_limit(self, tag_type: str) -> PermissionResult:
        """Prüft Rate-Limiting für bestimmte Actions."""
        # Manche Actions haben Limits (z.B. max 10 Searches pro Minute)
        rate_limits = {
            "search": {"max": 10, "window_seconds": 60},
            "browse": {"max": 5, "window_seconds": 60},
            "fetch_url": {"max": 10, "window_seconds": 60},
        }
        
        if tag_type not in rate_limits:
            return PermissionResult.allow()
        
        limit = rate_limits[tag_type]
        now = datetime.now()
        cutoff = now - timedelta(seconds=limit["window_seconds"])
        
        if tag_type not in self._rate_limits:
            self._rate_limits[tag_type] = []
        
        # Alte Einträge bereinigen
        self._rate_limits[tag_type] = [
            t for t in self._rate_limits[tag_type] if t > cutoff
        ]
        
        if len(self._rate_limits[tag_type]) >= limit["max"]:
            return PermissionResult.deny(
                f"Rate-Limit erreicht: max {limit['max']} {tag_type} pro {limit['window_seconds']}s"
            )
        
        # Call aufzeichnen
        self._rate_limits[tag_type].append(now)
        return PermissionResult.allow()

    def _is_sensitive_operation(self, tag_type: str, params: dict) -> bool:
        """Prüft ob eine Operation als sensitiv gilt."""
        # Shell-Commands sind immer sensitiv
        if tag_type == "shell_cmd":
            cmd = params.get("cmd", "")
            # Destruktive Befehle
            dangerous = ["rm ", "rmdir", "mv ", "dd ", ">", "sudo", "chmod", "chown"]
            return any(d in cmd for d in dangerous)
        
        # Datei-Operationen
        if tag_type in ("file_write", "file_delete"):
            return True
        
        return False

    def record_denial(self, tag_type: str) -> None:
        """Zeichnet eine Ablehnung auf (für Denial-Tracking)."""
        self._denial_tracking.record_denial(tag_type)
        logger.info("action_denied_recorded", tag=tag_type)


# ══════════════════════════════════════════════════════════════════════════
# Singleton Instance
# ══════════════════════════════════════════════════════════════════════════

_validator: Optional[ActionValidator] = None


def get_validator() -> ActionValidator:
    """Gibt die Singleton-Instanz des Validators zurück."""
    global _validator
    if _validator is None:
        _validator = ActionValidator()
    return _validator


async def validate_action(tag_type: str, params: dict) -> ValidationResult:
    """Convenience-Funktion für Validierung."""
    return await get_validator().validate(tag_type, params)


async def check_action_permission(
    tag_type: str,
    params: dict,
    user_context: Optional[dict] = None
) -> PermissionResult:
    """Convenience-Funktion für Permission-Check."""
    return await get_validator().check_permission(tag_type, params, user_context)
