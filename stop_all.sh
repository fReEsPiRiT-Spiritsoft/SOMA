#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – System Stoppen
# Stoppt alle SOMA-Services sauber.
# Usage: ./stop_all.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ───────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }

PIDDIR="$SCRIPT_DIR/.pids"

echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${B}  🛑 SOMA-AI Shutdown${NC}"
echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# ── 1. Brain Core stoppen ────────────────────────────────────────────────
echo -e "  ${B}Brain Core:${NC}"
if [ -f "$PIDDIR/brain_core.pid" ]; then
    PID=$(cat "$PIDDIR/brain_core.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        sleep 1
        # Falls noch lebt, SIGKILL
        kill -9 "$PID" 2>/dev/null || true
        ok "Brain Core gestoppt (PID $PID)"
    else
        warn "Brain Core war nicht aktiv (PID $PID)"
    fi
    rm -f "$PIDDIR/brain_core.pid"
else
    # Fallback: per Port
    PIDS=$(lsof -t -i:8100 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill -9 2>/dev/null || true
        ok "Brain Core gestoppt (Port 8100)"
    else
        warn "Brain Core war nicht aktiv"
    fi
fi

# ── 2. Django stoppen ────────────────────────────────────────────────────
echo -e "\n  ${B}Django SSOT:${NC}"
if [ -f "$PIDDIR/django.pid" ]; then
    PID=$(cat "$PIDDIR/django.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
        ok "Django gestoppt (PID $PID)"
    else
        warn "Django war nicht aktiv (PID $PID)"
    fi
    rm -f "$PIDDIR/django.pid"
else
    # Fallback: per Port
    PIDS=$(lsof -t -i:8200 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill -9 2>/dev/null || true
        ok "Django gestoppt (Port 8200)"
    else
        warn "Django war nicht aktiv"
    fi
fi

# ── 3. arecord Prozesse stoppen ──────────────────────────────────────────
echo -e "\n  ${B}Audio Capture:${NC}"
ARECORD_PIDS=$(pgrep -f "arecord" 2>/dev/null || true)
if [ -n "$ARECORD_PIDS" ]; then
    echo "$ARECORD_PIDS" | xargs kill 2>/dev/null || true
    ok "arecord Prozesse gestoppt"
else
    warn "Keine arecord Prozesse aktiv"
fi

# ── 4. Docker Container (optional) ───────────────────────────────────────
echo -e "\n  ${B}Docker Container:${NC}"
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    read -p "  Docker-Container auch stoppen? (j/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[JjYy]$ ]]; then
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" stop 2>/dev/null
        ok "Docker-Container gestoppt (PostgreSQL, Redis, Mosquitto)"
    else
        warn "Docker-Container laufen weiter"
    fi
else
    warn "Docker nicht verfügbar"
fi

# ── 5. Cleanup ───────────────────────────────────────────────────────────
echo -e "\n  ${B}Cleanup:${NC}"
rm -f "$PIDDIR"/*.pid 2>/dev/null
ok "PID-Dateien aufgeräumt"

# ── Zusammenfassung ──────────────────────────────────────────────────────
echo -e "\n${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${G}  ✓ SOMA-AI ist offline${NC}"
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "\n  Neu starten: ${C}./start_soma.sh${NC}\n"
