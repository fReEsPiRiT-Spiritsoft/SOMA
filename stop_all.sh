#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – Vollständiger System-Shutdown
# Stoppt ALLE SOMA-Prozesse und -Container rückstandslos.
#
# Usage:
#   ./stop_all.sh               # Alles stoppen inkl. Docker
#   ./stop_all.sh --keep-docker # Python/Audio stoppen, Docker läuft weiter
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Argument Handling ────────────────────────────────────────────────────
KEEP_DOCKER=0
for arg in "$@"; do
    case "$arg" in
        --keep-docker) KEEP_DOCKER=1 ;;
    esac
done

# ── Colors ───────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }
hdr()  { echo -e "\n${C}── $1 ──${NC}"; }

PIDDIR="$SCRIPT_DIR/.pids"
BRAIN_PORT="${BRAIN_CORE_PORT:-8100}"
DJANGO_PORT_NUM="${DJANGO_PORT:-8200}"

echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${B}  🛑 SOMA-AI Vollständiger Shutdown${NC}"
echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# ──────────────────────────────────────────────────────────────────────────
# Hilfsfunktion: Prozess per PID + Prozessname vollständig beenden
# Behandelt Django's Doppelprozess (Reloader + Worker) korrekt.
# ──────────────────────────────────────────────────────────────────────────
kill_by_pattern() {
    local pattern="$1"
    local pids
    pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -SIGTERM 2>/dev/null || true
        sleep 1
        # Noch lebende Prozesse hart beenden
        pids=$(pgrep -f "$pattern" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
        fi
        return 0
    fi
    return 1
}

# ── 1. Brain Core (FastAPI/Uvicorn) ─────────────────────────────────────
hdr "1/6 Brain Core"

STOPPED=0
if [ -f "$PIDDIR/brain_core.pid" ]; then
    PID=$(cat "$PIDDIR/brain_core.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill -SIGTERM "$PID" 2>/dev/null || true
        sleep 2
        kill -9 "$PID" 2>/dev/null || true
        ok "Brain Core gestoppt (PID $PID)"
        STOPPED=1
    fi
    rm -f "$PIDDIR/brain_core.pid"
fi

# Sweep: alle verbleibenden brain_core Prozesse (uvicorn workers etc.)
if kill_by_pattern "brain_core\.main"; then
    ok "Brain Core Worker-Prozesse beendet"
    STOPPED=1
fi

# Fallback: Port 8100
PORT_PIDS=$(lsof -t -i:"$BRAIN_PORT" 2>/dev/null || true)
if [ -n "$PORT_PIDS" ]; then
    echo "$PORT_PIDS" | xargs kill -9 2>/dev/null || true
    ok "Port $BRAIN_PORT freigegeben"
    STOPPED=1
fi

[ $STOPPED -eq 0 ] && warn "Brain Core war nicht aktiv"

# ── 2. Django SSOT (Reloader + Worker — beide Prozesse!) ─────────────────
hdr "2/6 Django SSOT"

STOPPED=0
if [ -f "$PIDDIR/django.pid" ]; then
    PID=$(cat "$PIDDIR/django.pid")
    if kill -0 "$PID" 2>/dev/null; then
        # SIGTERM an Parent → Django-Reloader leitet es weiter
        kill -SIGTERM "$PID" 2>/dev/null || true
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
        ok "Django Parent-Prozess gestoppt (PID $PID)"
        STOPPED=1
    fi
    rm -f "$PIDDIR/django.pid"
fi

# Django spawnt immer einen Child-Prozess (Auto-Reloader).
# Beide müssen explizit beendet werden.
if kill_by_pattern "manage\.py runserver"; then
    ok "Django Reloader + Worker vollständig beendet"
    STOPPED=1
fi

# Fallback: Port 8200
PORT_PIDS=$(lsof -t -i:"$DJANGO_PORT_NUM" 2>/dev/null || true)
if [ -n "$PORT_PIDS" ]; then
    echo "$PORT_PIDS" | xargs kill -9 2>/dev/null || true
    ok "Port $DJANGO_PORT_NUM freigegeben"
    STOPPED=1
fi

[ $STOPPED -eq 0 ] && warn "Django war nicht aktiv"

# ── 2b. Cloudflare Tunnel ───────────────────────────────────────────────
hdr "2b/6 Cloudflare Tunnel"

STOPPED=0
if [ -f "$PIDDIR/cloudflared.pid" ]; then
    PID=$(cat "$PIDDIR/cloudflared.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill -SIGTERM "$PID" 2>/dev/null || true
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
        ok "Cloudflare Tunnel gestoppt (PID $PID)"
        STOPPED=1
    fi
    rm -f "$PIDDIR/cloudflared.pid"
fi
if kill_by_pattern "cloudflared tunnel"; then
    ok "Cloudflare Tunnel-Prozesse beendet"
    STOPPED=1
fi
[ $STOPPED -eq 0 ] && warn "Cloudflare Tunnel war nicht aktiv"

# ── 3. Audio Capture (arecord + SOMA-Audio-Threads) ─────────────────────
hdr "3/6 Audio Capture"

STOPPED=0
if kill_by_pattern "arecord"; then
    ok "arecord Prozesse gestoppt"
    STOPPED=1
fi
# SOMA's audio_capture.py kann eigenständig laufen
if kill_by_pattern "audio_capture"; then
    ok "audio_capture Prozesse gestoppt"
    STOPPED=1
fi
[ $STOPPED -eq 0 ] && warn "Keine Audio-Prozesse aktiv"

# ── 4. Sandbox Docker Container (vom Evolution Lab) ──────────────────────
hdr "4/6 Sandbox Container"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    # Plugin-Sandbox Container die hängen geblieben sind
    SANDBOX_CONTAINERS=$(docker ps -q --filter "name=soma_sandbox_" 2>/dev/null || true)
    if [ -n "$SANDBOX_CONTAINERS" ]; then
        echo "$SANDBOX_CONTAINERS" | xargs docker rm -f 2>/dev/null || true
        COUNT=$(echo "$SANDBOX_CONTAINERS" | wc -l)
        ok "$COUNT hängende Sandbox-Container entfernt"
    else
        ok "Keine hängenden Sandbox-Container"
    fi

    # Sandbox Temp-Dateien aufräumen (generierte Test-Skripte)
    SANDBOX_DIR="$SCRIPT_DIR/evolution_lab/sandbox_env"
    if [ -d "$SANDBOX_DIR" ]; then
        TMPFILES=$(find "$SANDBOX_DIR" -maxdepth 1 -name "*.py" ! -name ".gitkeep" 2>/dev/null | wc -l)
        if [ "$TMPFILES" -gt 0 ]; then
            find "$SANDBOX_DIR" -maxdepth 1 -name "*.py" ! -name ".gitkeep" -delete 2>/dev/null || true
            ok "Sandbox Temp-Dateien bereinigt ($TMPFILES Dateien)"
        else
            ok "Sandbox sauber"
        fi
    fi
else
    warn "Docker nicht verfügbar – Sandbox-Cleanup übersprungen"
fi

# ── 5. Docker Infrastruktur (PostgreSQL, Redis, Mosquitto, Asterisk) ──────
hdr "5/6 Docker Infrastruktur"

if [ $KEEP_DOCKER -eq 1 ]; then
    warn "Docker-Container laufen weiter (--keep-docker)"
elif command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" stop 2>/dev/null
    COMPOSE_RC=$?
    if [ $COMPOSE_RC -eq 0 ]; then
        ok "Docker-Container gestoppt (PostgreSQL, Redis, Mosquitto)"
    else
        # Einzeln stoppen als Fallback
        for cname in soma-postgres soma-redis soma-mosquitto soma-asterisk; do
            STATUS=$(docker inspect -f '{{.State.Status}}' "$cname" 2>/dev/null || echo "none")
            if [ "$STATUS" = "running" ]; then
                docker stop "$cname" 2>/dev/null || true
                ok "$cname gestoppt"
            fi
        done
    fi
else
    warn "Docker nicht verfügbar"
fi

# ── 6. Finaler Prozess-Sweep + Cleanup ───────────────────────────────────
hdr "6/6 Finaler Sweep & Cleanup"

# Letzter Check: Irgendwelche SOMA-Python-Prozesse noch aktiv?
REMAINING=$(pgrep -f "brain_core\|manage\.py runserver\|soma_face\|audio_capture" 2>/dev/null | wc -l || echo "0")
if [ "$REMAINING" -gt 0 ]; then
    warn "$REMAINING SOMA-Prozesse noch aktiv — erzwinge Beendigung..."
    pgrep -f "brain_core\|manage\.py runserver\|soma_face\|audio_capture" 2>/dev/null \
        | xargs kill -9 2>/dev/null || true
    ok "Alle verbleibenden SOMA-Prozesse beendet"
else
    ok "Keine SOMA-Prozesse mehr aktiv"
fi

# PID-Dateien aufräumen
rm -f "$PIDDIR"/*.pid 2>/dev/null
ok "PID-Dateien aufgeräumt"

# Ports final prüfen
for port in "$BRAIN_PORT" "$DJANGO_PORT_NUM"; do
    if lsof -t -i:"$port" &>/dev/null 2>&1; then
        warn "Port $port noch belegt – manuell prüfen: lsof -i:$port"
    fi
done

# ── Zusammenfassung ──────────────────────────────────────────────────────
echo ""
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${G}  ✓ SOMA-AI vollständig offline — kein Prozess zurückgeblieben${NC}"
echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "\n  Neu starten:          ${C}./start_soma.sh${NC}"
echo -e "  Docker weiter laufen: ${C}./stop_all.sh --keep-docker${NC}\n"
