#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – Vollständiger Systemstart
# ============================================================================
# Startet alle Services in der richtigen Reihenfolge:
#   1. Docker Daemon (falls nötig)
#   2. PostgreSQL, Redis, Mosquitto (Docker)
#   3. Ollama (LLM Runtime)
#   4. Django SSOT (Port 8200)
#   5. Brain Core (Port 8100)
#
# Usage:
#   ./start_soma.sh           # Alles starten
#   ./start_soma.sh --status  # Status prüfen
#   ./start_soma.sh --logs    # Live Logs anzeigen
#
# Stoppen: ./stop_all.sh
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors & Helpers ─────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }
head() { echo -e "\n${C}── $1 ──${NC}"; }

# ── Directories ──────────────────────────────────────────────────────────
PIDDIR="$SCRIPT_DIR/.pids"
LOGDIR="$SCRIPT_DIR/.logs"
mkdir -p "$PIDDIR" "$LOGDIR"

# ── Load Environment ─────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ============================================================================
# ASCII Banner
# ============================================================================
banner() {
    echo -e "${C}"
    echo "  ███████╗ ██████╗ ███╗   ███╗ █████╗ "
    echo "  ██╔════╝██╔═══██╗████╗ ████║██╔══██╗"
    echo "  ███████╗██║   ██║██╔████╔██║███████║"
    echo "  ╚════██║██║   ██║██║╚██╔╝██║██╔══██║"
    echo "  ███████║╚██████╔╝██║ ╚═╝ ██║██║  ██║"
    echo "  ╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝"
    echo -e "${NC}  ${B}Adaptive Ambient AI${NC} – Das lebendige Zuhause\n"
}

# ============================================================================
# STATUS – Systemstatus prüfen
# ============================================================================
show_status() {
    echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${B}  📊 SOMA-AI Systemstatus${NC}"
    echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Docker Container
    echo -e "\n  ${B}Docker Container:${NC}"
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        for svc in soma-postgres soma-redis soma-mosquitto; do
            STATUS=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "nicht gefunden")
            if [ "$STATUS" = "running" ]; then
                HEALTH=$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "ok")
                ok "$svc: ${G}running${NC} ($HEALTH)"
            else
                fail "$svc: $STATUS"
            fi
        done
    else
        fail "Docker nicht erreichbar"
    fi

    # Ollama
    echo -e "\n  ${B}Ollama (LLM):${NC}"
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        MODELS=$(curl -sf http://localhost:11434/api/tags | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "?")
        ok "Online – Modelle: ${C}$MODELS${NC}"
    else
        fail "Nicht erreichbar (http://localhost:11434)"
    fi

    # Brain Core
    echo -e "\n  ${B}Brain Core (FastAPI):${NC}"
    if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
        METRICS=$(curl -sf http://localhost:8100/api/v1/health | python3 -c "
import sys,json
d=json.load(sys.stdin)
m=d.get('metrics',{})
print(f\"CPU {m.get('cpu_percent',0):.0f}% | RAM {m.get('ram_percent',0):.0f}% | {m.get('load_level','?')}\")" 2>/dev/null || echo "ok")
        ok "Online (Port 8100) – $METRICS"
        
        # Voice Status
        VOICE=$(curl -sf http://localhost:8100/api/v1/voice | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f\"🎤 {d.get('status','?')} | Transkriptionen: {d.get('transcriptions',0)}\")" 2>/dev/null || echo "")
        [ -n "$VOICE" ] && ok "$VOICE"
    else
        fail "Nicht erreichbar (http://localhost:8100)"
    fi

    # Django
    echo -e "\n  ${B}Django SSOT:${NC}"
    if curl -sf http://localhost:8200/dashboard/ >/dev/null 2>&1; then
        ok "Online (Port 8200)"
    elif curl -sf http://localhost:8200/ >/dev/null 2>&1; then
        ok "Online (Port 8200)"
    else
        fail "Nicht erreichbar (http://localhost:8200)"
    fi

    # Audio Hardware
    echo -e "\n  ${B}Audio Hardware:${NC}"
    if arecord -l 2>/dev/null | grep -qi "scarlett\|focusrite\|usb"; then
        DEVICE=$(arecord -l 2>/dev/null | grep -i "scarlett\|focusrite\|usb" | head -1)
        ok "Erkannt: $DEVICE"
    elif arecord -l 2>/dev/null | grep -q "card"; then
        ok "Audio-Device verfügbar"
    else
        warn "Kein Audio-Device gefunden"
    fi

    # Memory System
    echo -e "\n  ${B}Memory System:${NC}"
    MEMORY_FILE="$SCRIPT_DIR/brain_core/data/soma_memory.json"
    if [ -f "$MEMORY_FILE" ]; then
        ENTRIES=$(python3 -c "import json; d=json.load(open('$MEMORY_FILE')); print(sum(len(v) for v in d.values()))" 2>/dev/null || echo "0")
        ok "Aktiv – $ENTRIES Erinnerungen gespeichert"
    else
        warn "Noch keine Erinnerungen"
    fi

    # Evolution Lab
    echo -e "\n  ${B}Evolution Lab:${NC}"
    PLUGINS=$(ls -1 "$SCRIPT_DIR/evolution_lab/generated_plugins/"*.py 2>/dev/null | wc -l || echo "0")
    ok "$PLUGINS Plugins installiert"

    echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    exit 0
}

# ============================================================================
# LOGS – Live Logs anzeigen
# ============================================================================
show_logs() {
    echo -e "\n${C}━━━ SOMA-AI Live Logs (Ctrl+C zum Beenden) ━━━${NC}\n"
    tail -f "$LOGDIR/brain_core.log" "$LOGDIR/django.log" 2>/dev/null || \
        tail -f "$LOGDIR/brain_core.log" 2>/dev/null || \
        echo "Keine Logs gefunden. Erst ./start_soma.sh ausführen."
    exit 0
}

# ============================================================================
# Argument Handling
# ============================================================================
case "${1:-start}" in
    --status|-s|status)  show_status ;;
    --logs|-l|logs)      show_logs ;;
    --help|-h)
        echo "Usage: $0 [--status|--logs|--help]"
        echo "  (keine Argumente) = System starten"
        echo "  --status, -s      = Systemstatus anzeigen"
        echo "  --logs, -l        = Live Logs anzeigen"
        exit 0
        ;;
    start|--start|"") ;;
    *)
        echo "Unbekannte Option: $1"
        echo "Usage: $0 [--status|--logs|--help]"
        exit 1
        ;;
esac

# ============================================================================
# START – Boot-Sequenz
# ============================================================================
banner

# ── Prüfe ob bereits läuft ───────────────────────────────────────────────
if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
    echo -e "  ${Y}SOMA-AI läuft bereits!${NC}"
    echo -e "  Status: ${C}./start_soma.sh --status${NC}"
    echo -e "  Stoppen: ${C}./stop_all.sh${NC}"
    echo ""
    exit 0
fi

# ── 1. Docker Daemon ────────────────────────────────────────────────────
head "1/5 Docker Daemon"

if ! docker info &>/dev/null 2>&1; then
    echo -e "  Docker Daemon starten (sudo nötig)..."
    sudo systemctl start docker
    sleep 2
    if docker info &>/dev/null 2>&1; then
        ok "Docker Daemon gestartet"
    else
        fail "Docker Daemon konnte nicht gestartet werden!"
        fail "Manuell starten: sudo systemctl start docker"
        exit 1
    fi
else
    ok "Docker Daemon läuft"
fi

# ── 2. Infrastruktur-Container ──────────────────────────────────────────
head "2/5 Infrastruktur (PostgreSQL, Redis, MQTT)"

# Container starten
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d postgres redis mosquitto 2>&1 | grep -v "^$" | head -5

# Warte auf Health-Checks
echo -e "  Warte auf Services..."
READY=0
for i in $(seq 1 30); do
    PG_OK=$(docker inspect -f '{{.State.Health.Status}}' soma-postgres 2>/dev/null || echo "none")
    REDIS_OK=$(docker inspect -f '{{.State.Health.Status}}' soma-redis 2>/dev/null || echo "none")
    
    if [ "$PG_OK" = "healthy" ] && [ "$REDIS_OK" = "healthy" ]; then
        READY=1
        break
    fi
    sleep 1
done

if [ $READY -eq 1 ]; then
    ok "PostgreSQL: healthy"
    ok "Redis: healthy"
    ok "Mosquitto: running"
else
    warn "Services brauchen noch einen Moment..."
fi

# ── 3. Ollama ───────────────────────────────────────────────────────────
head "3/5 Ollama (LLM Runtime)"

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama läuft bereits"
else
    if systemctl is-active --quiet ollama 2>/dev/null; then
        ok "Ollama Service aktiv"
    else
        echo -e "  Starte Ollama..."
        sudo systemctl start ollama 2>/dev/null || ollama serve &>/dev/null &
        sleep 3
    fi
    
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama gestartet"
    else
        warn "Ollama braucht noch einen Moment..."
    fi
fi

# Modelle anzeigen
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    MODELS=$(curl -sf http://localhost:11434/api/tags | python3 -c "
import sys,json
d=json.load(sys.stdin)
names=[m['name'] for m in d.get('models',[])]
print(', '.join(names) if names else 'keine')" 2>/dev/null || echo "?")
    ok "Modelle: ${C}$MODELS${NC}"
fi

# ── 4. Python venv ──────────────────────────────────────────────────────
head "4/5 Python Environment"

VENV="$SCRIPT_DIR/.venv"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
    ok "venv aktiviert: $(python --version 2>&1)"
else
    fail "venv nicht gefunden!"
    fail "Erst ausführen: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# ── 5a. Django SSOT ─────────────────────────────────────────────────────
head "5/5 SOMA Services"

echo -e "  ${B}Django SSOT (Port 8200):${NC}"
if curl -sf http://localhost:8200/ >/dev/null 2>&1; then
    ok "Django läuft bereits"
else
    # Django starten
    cd "$SCRIPT_DIR"
    USE_SQLITE=true nohup python brain_memory_ui/manage.py runserver 0.0.0.0:8200 \
        > "$LOGDIR/django.log" 2>&1 &
    DJANGO_PID=$!
    echo "$DJANGO_PID" > "$PIDDIR/django.pid"
    sleep 2

    if curl -sf http://localhost:8200/ >/dev/null 2>&1; then
        ok "Django gestartet (PID $DJANGO_PID)"
    else
        warn "Django startet... (Log: .logs/django.log)"
    fi
fi

# ── 5b. Brain Core ──────────────────────────────────────────────────────
echo -e "\n  ${B}Brain Core (Port 8100):${NC}"
if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
    ok "Brain Core läuft bereits"
else
    cd "$SCRIPT_DIR"
    nohup python -m brain_core.main \
        > "$LOGDIR/brain_core.log" 2>&1 &
    BRAIN_PID=$!
    echo "$BRAIN_PID" > "$PIDDIR/brain_core.pid"
    
    echo -e "  Warte auf Brain Core..."
    for i in $(seq 1 15); do
        if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
            ok "Brain Core gestartet (PID $BRAIN_PID) 🧠"
            break
        fi
        sleep 1
    done
    
    if ! curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
        warn "Brain Core braucht noch... (Log: .logs/brain_core.log)"
    fi
fi

# ============================================================================
# Zusammenfassung
# ============================================================================
sleep 2
BRAIN_OK=$(curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1 && echo "1" || echo "0")
DJANGO_OK=$(curl -sf http://localhost:8200/ >/dev/null 2>&1 && echo "1" || echo "0")

echo ""
if [ "$BRAIN_OK" = "1" ]; then
    echo -e "${G}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${G}║${NC}  ${B}🧠 SOMA-AI ist online!${NC}                                    ${G}║${NC}"
    echo -e "${G}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${B}Dashboard:${NC}      ${C}http://localhost:8200/dashboard/${NC}          ${G}║${NC}"
    echo -e "${G}║${NC}  ${B}API Docs:${NC}       ${C}http://localhost:8100/docs${NC}                ${G}║${NC}"
    echo -e "${G}║${NC}  ${B}Health:${NC}         ${C}http://localhost:8100/api/v1/health${NC}       ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Soma hört jetzt dauerhaft zu! 🎤${NC}                          ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Sage \"Soma, ...\" um zu sprechen.${NC}                          ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${G}║${NC}  Status:  ${C}./start_soma.sh --status${NC}                         ${G}║${NC}"
    echo -e "${G}║${NC}  Logs:    ${C}./start_soma.sh --logs${NC}                           ${G}║${NC}"
    echo -e "${G}║${NC}  Stop:    ${C}./stop_all.sh${NC}                                    ${G}║${NC}"
    echo -e "${G}╚══════════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${Y}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${Y}║${NC}  ${B}⏳ SOMA-AI startet noch...${NC}                                 ${Y}║${NC}"
    echo -e "${Y}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${Y}║${NC}  Brain Core braucht noch einen Moment.                       ${Y}║${NC}"
    echo -e "${Y}║${NC}  Log prüfen: ${C}tail -f .logs/brain_core.log${NC}                   ${Y}║${NC}"
    echo -e "${Y}║${NC}  Status:     ${C}./start_soma.sh --status${NC}                       ${Y}║${NC}"
    echo -e "${Y}╚══════════════════════════════════════════════════════════════╝${NC}"
fi
echo ""
