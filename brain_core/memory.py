"""
SOMA-AI Memory System — Persistenter Wissensspeicher
=====================================================
Soma kann wichtige Informationen speichern und später abrufen.

Features:
  ✅ JSON-basierte Persistenz (kein DB nötig)
  ✅ Kategorisierte Einträge (user_info, preferences, facts, etc.)
  ✅ Zeitstempel für alle Einträge
  ✅ Einfache API für LLM-Integration
  ✅ Automatische Extraktion von wichtigen Infos
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("soma.memory")

# ── Speicherort ─────────────────────────────────────────────────────────

MEMORY_FILE = Path(__file__).parent.parent / "data" / "soma_memory.json"


class MemoryCategory:
    """Kategorien für Erinnerungen."""
    USER_INFO = "user_info"          # Infos über Nutzer (Name, Beruf, etc.)
    PREFERENCES = "preferences"       # Vorlieben (Musik, Temperatur, etc.)
    FACTS = "facts"                   # Allgemeine Fakten
    RELATIONSHIPS = "relationships"   # Beziehungen zwischen Personen
    ROUTINES = "routines"             # Gewohnheiten, Tagesabläufe
    IMPORTANT = "important"           # Explizit als wichtig markiert


class SomaMemory:
    """
    Persistenter Wissensspeicher für Soma.
    
    Soma kann sich Dinge merken und später darauf zugreifen.
    """
    
    def __init__(self, memory_file: Path = MEMORY_FILE):
        self.memory_file = memory_file
        self._ensure_file()
        self._memories: dict[str, list[dict]] = self._load()
        logger.info("memory_loaded", entries=self._count_entries())
    
    def _ensure_file(self) -> None:
        """Erstellt Verzeichnis und Datei falls nötig."""
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            self.memory_file.write_text("{}")
            logger.info("memory_file_created", path=str(self.memory_file))
    
    def _load(self) -> dict[str, list[dict]]:
        """Lädt Erinnerungen aus Datei."""
        try:
            data = json.loads(self.memory_file.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, Exception) as e:
            logger.error("memory_load_error", error=str(e))
            return {}
    
    def _save(self) -> None:
        """Speichert Erinnerungen in Datei."""
        try:
            self.memory_file.write_text(
                json.dumps(self._memories, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.error("memory_save_error", error=str(e))
    
    def _count_entries(self) -> int:
        """Zählt alle Einträge."""
        return sum(len(entries) for entries in self._memories.values())
    
    # ── Public API ──────────────────────────────────────────────────────
    
    def remember(
        self,
        content: str,
        category: str = MemoryCategory.FACTS,
        source: str = "conversation",
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Speichert eine neue Erinnerung.
        
        Args:
            content: Was sich Soma merken soll
            category: Kategorie (user_info, preferences, etc.)
            source: Woher die Info kommt (conversation, explicit, etc.)
            metadata: Zusätzliche Infos (z.B. Sprecher)
        
        Returns:
            Die gespeicherte Erinnerung
        """
        entry = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "metadata": metadata or {},
        }
        
        if category not in self._memories:
            self._memories[category] = []
        
        # Duplikate vermeiden (gleicher Content)
        existing_contents = [e["content"] for e in self._memories[category]]
        if content not in existing_contents:
            self._memories[category].append(entry)
            self._save()
            logger.info(
                "memory_stored",
                category=category,
                content_preview=content[:50] + "..." if len(content) > 50 else content,
            )
        else:
            logger.debug("memory_duplicate_skipped", content_preview=content[:30])
        
        return entry
    
    def recall(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Ruft Erinnerungen ab.
        
        Args:
            category: Nur aus dieser Kategorie (optional)
            query: Suchbegriff im Content (optional)
            limit: Maximale Anzahl Ergebnisse
        
        Returns:
            Liste von Erinnerungen
        """
        results = []
        
        categories = [category] if category else list(self._memories.keys())
        
        for cat in categories:
            if cat not in self._memories:
                continue
            for entry in self._memories[cat]:
                if query:
                    if query.lower() in entry["content"].lower():
                        results.append({**entry, "category": cat})
                else:
                    results.append({**entry, "category": cat})
        
        # Neueste zuerst
        results.sort(key=lambda x: x["timestamp"], reverse=True)
        return results[:limit]
    
    def recall_for_context(self, prompt: str) -> list[dict]:
        """
        Findet relevante Erinnerungen für einen Prompt.
        Extrahiert Keywords und sucht danach.
        
        Args:
            prompt: Der aktuelle User-Prompt
        
        Returns:
            Relevante Erinnerungen
        """
        # Einfache Keyword-Extraktion
        # Entferne Stoppwörter und suche nach übrigen Wörtern
        stopwords = {
            'der', 'die', 'das', 'ein', 'eine', 'ist', 'sind', 'war', 'waren',
            'und', 'oder', 'aber', 'wenn', 'weil', 'dass', 'ob', 'wie', 'was',
            'wer', 'wo', 'wann', 'warum', 'welche', 'welcher', 'welches',
            'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr', 'mich', 'dich',
            'mir', 'dir', 'sich', 'uns', 'euch', 'ihm', 'ihr', 'ihnen',
            'mein', 'dein', 'sein', 'unser', 'euer', 'meinen', 'deinen',
            'auf', 'in', 'an', 'zu', 'von', 'mit', 'bei', 'nach', 'für',
            'soma', 'sommer', 'summer', 'bitte', 'danke', 'mal', 'noch',
            'ja', 'nein', 'nicht', 'kein', 'keine', 'keinen', 'schon',
            'kannst', 'können', 'soll', 'sollst', 'muss', 'musst', 'will',
            'hast', 'hat', 'haben', 'hatte', 'hatten', 'bin', 'bist',
            'sag', 'sagen', 'erzähl', 'erzählen', 'weißt', 'wissen',
        }
        
        words = re.findall(r'\b[a-züäöß]+\b', prompt.lower())
        keywords = [w for w in words if w not in stopwords and len(w) > 2]
        
        relevant = []
        for keyword in keywords[:5]:  # Max 5 Keywords
            found = self.recall(query=keyword, limit=3)
            for entry in found:
                if entry not in relevant:
                    relevant.append(entry)
        
        return relevant[:5]  # Max 5 relevante Erinnerungen
    
    def forget(self, category: str, content_match: str) -> bool:
        """
        Löscht eine Erinnerung.
        
        Args:
            category: Kategorie
            content_match: Teil des Contents zum Matchen
        
        Returns:
            True wenn gelöscht, False wenn nicht gefunden
        """
        if category not in self._memories:
            return False
        
        original_count = len(self._memories[category])
        self._memories[category] = [
            e for e in self._memories[category]
            if content_match.lower() not in e["content"].lower()
        ]
        
        if len(self._memories[category]) < original_count:
            self._save()
            logger.info("memory_forgotten", category=category, match=content_match)
            return True
        return False
    
    def get_summary_for_prompt(self) -> str:
        """
        Generiert eine Zusammenfassung aller Erinnerungen für den System-Prompt.
        
        Returns:
            Formatierter String mit wichtigen Erinnerungen
        """
        if not self._memories:
            return ""
        
        lines = ["## Dein Wissen über diese Nutzer/Umgebung:"]
        
        # Priorität: user_info > important > preferences > rest
        priority_order = [
            MemoryCategory.USER_INFO,
            MemoryCategory.IMPORTANT,
            MemoryCategory.PREFERENCES,
            MemoryCategory.RELATIONSHIPS,
            MemoryCategory.ROUTINES,
            MemoryCategory.FACTS,
        ]
        
        for cat in priority_order:
            if cat in self._memories and self._memories[cat]:
                cat_display = {
                    MemoryCategory.USER_INFO: "👤 Nutzer-Infos",
                    MemoryCategory.IMPORTANT: "⭐ Wichtig",
                    MemoryCategory.PREFERENCES: "💜 Vorlieben",
                    MemoryCategory.RELATIONSHIPS: "👥 Beziehungen",
                    MemoryCategory.ROUTINES: "🔄 Routinen",
                    MemoryCategory.FACTS: "📌 Fakten",
                }.get(cat, cat)
                
                lines.append(f"\n### {cat_display}:")
                for entry in self._memories[cat][-5:]:  # Letzte 5 pro Kategorie
                    lines.append(f"- {entry['content']}")
        
        return "\n".join(lines)
    
    def should_remember(self, text: str) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Analysiert ob ein Text wichtige Infos enthält die gespeichert werden sollten.
        
        Returns:
            (should_save, category, extracted_info)
        """
        t = text.lower()
        
        # Explizite Marker
        explicit_markers = [
            "merk dir", "vergiss nicht", "wichtig:", "erinner dich",
            "speicher das", "notier dir", "remember", "don't forget",
        ]
        if any(m in t for m in explicit_markers):
            # Extrahiere was nach dem Marker kommt
            for marker in explicit_markers:
                if marker in t:
                    idx = t.find(marker)
                    content = text[idx + len(marker):].strip()
                    if content:
                        return True, MemoryCategory.IMPORTANT, content
        
        # Vorstellungen erkennen
        intro_patterns = [
            r"ich bin (\w+)",
            r"ich heiße (\w+)",
            r"mein name ist (\w+)",
            r"ich arbeite als (\w+)",
            r"ich bin (\w+) jahre alt",
        ]
        for pattern in intro_patterns:
            match = re.search(pattern, t)
            if match:
                return True, MemoryCategory.USER_INFO, text
        
        # Vorlieben erkennen
        pref_patterns = [
            r"ich mag (.*?)(?:\.|,|$)",
            r"ich liebe (.*?)(?:\.|,|$)",
            r"meine lieblings",
            r"ich bevorzuge",
            r"ich höre gerne",
        ]
        for pattern in pref_patterns:
            if re.search(pattern, t):
                return True, MemoryCategory.PREFERENCES, text
        
        # Entwickler/Creator Info (speziell für Patrick)
        if "entwickler" in t or "entwickelt" in t or "programmiert" in t:
            if "ich" in t or "dein" in t:
                return True, MemoryCategory.USER_INFO, text
        
        return False, None, None


# ── Globale Instanz ─────────────────────────────────────────────────────

_memory_instance: Optional[SomaMemory] = None


def get_memory() -> SomaMemory:
    """Gibt die globale Memory-Instanz zurück."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = SomaMemory()
    return _memory_instance
