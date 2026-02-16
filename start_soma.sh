#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – Täglicher Systemstart
# Startet alle Services nach einem Neustart / Herunterfahren.
# Usage: ./start_soma.sh          (alle Services)
#        ./start_soma.sh --stop   (alles stoppen)
#        ./start_soma.sh --status (Status prüfen)
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ───────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }
head() { echo -e "\n${C}── $1 ──${NC}"; }

PIDDIR="$SCRIPT_DIR/.pids"
mkdir -p "$PIDDIR"

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
    echo -e "${NC}  Adaptive Ambient OS${B} – Systemstart${NC}\n"
}

# ============================================================================
# STOP – Alles herunterfahren
# ============================================================================
stop_soma() {
    head "SOMA-AI stoppen"

    # Brain Core
    if [ -f "$PIDDIR/brain_core.pid" ]; then
        PID=$(cat "$PIDDIR/brain_core.pid")
        if kill "$PID" 2>/dev/null; then
            ok "Brain Core gestoppt (PID $PID)"
        fi
        rm -f "$PIDDIR/brain_core.pid"
    else
        # Fallback: per Prozessname
        pkill -f "python -m brain_core.main" 2>/dev/null && ok "Brain Core gestoppt" || true
    fi

    # Django
    if [ -f "$PIDDIR/django.pid" ]; then
        PID=$(cat "$PIDDIR/django.pid")
        if kill "$PID" 2>/dev/null; then
            ok "Django gestoppt (PID $PID)"
        fi
        rm -f "$PIDDIR/django.pid"
    else
        pkill -f "manage.py runserver" 2>/dev/null && ok "Django gestoppt" || true
    fi

    # Audio Capture
    if [ -f "$PIDDIR/audio_capture.pid" ]; then
        PID=$(cat "$PIDDIR/audio_capture.pid")
        if kill "$PID" 2>/dev/null; then
            ok "Audio Capture gestoppt (PID $PID)"
        fi
        rm -f "$PIDDIR/audio_capture.pid"
    fi

    # Docker
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" stop 2>/dev/null
        ok "Docker-Services gestoppt"
    fi

    echo -e "\n${G}SOMA-AI ist offline.${NC}\n"
    exit 0
}

# ============================================================================
# STATUS – Alles prüfen
# ============================================================================
status_soma() {
    head "SOMA-AI Status"

    # Docker
    echo -e "\n  ${B}Docker-Container:${NC}"
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        for svc in soma-postgres soma-redis soma-mosquitto; do
            STATUS=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "nicht gefunden")
            HEALTH=$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "-")
            if [ "$STATUS" = "running" ]; then
                ok "$svc: running ($HEALTH)"
            else
                fail "$svc: $STATUS"
            fi
        done
    else
        fail "Docker nicht erreichbar"
    fi

    # Ollama
    echo -e "\n  ${B}Ollama:${NC}"
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        MODELS=$(curl -sf http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null)
        ok "Ollama online – Modelle: $MODELS"
    else
        fail "Ollama nicht erreichbar"
    fi

    # Brain Core
    echo -e "\n  ${B}Brain Core (FastAPI):${NC}"
    if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
        METRICS=$(curl -sf http://localhost:8100/api/v1/health | python3 -c "
import sys,json; d=json.load(sys.stdin)
m=d.get('metrics',{})
print(f\"CPU {m.get('cpu_percent','-')}% | RAM {m.get('ram_percent','-')}% | Load: {m.get('load_level','-')}\")" 2>/dev/null)
        ok "Brain Core online – $METRICS"
    else
        fail "Brain Core nicht erreichbar (Port 8100)"
    fi

    # Django
    echo -e "\n  ${B}Django SSOT:${NC}"
    if curl -sf http://localhost:8200/ >/dev/null 2>&1; then
        ok "Django online (Port 8200)"
    else
        fail "Django nicht erreichbar (Port 8200)"
    fi

    # Audio
    echo -e "\n  ${B}Audio-Hardware:${NC}"
    if arecord -l 2>/dev/null | grep -qi "scarlett\|focusrite"; then
        ok "Focusrite Scarlett erkannt"
    else
        warn "Kein Focusrite-Interface gefunden"
    fi

    echo ""
    exit 0
}

# ============================================================================
# Argument Handling
# ============================================================================
case "${1:-start}" in
    --stop|-s)   stop_soma ;;
    --status|-t) status_soma ;;
    start|--start) ;;
    *)
        echo "Usage: $0 [--start|--stop|--status]"
        exit 1
        ;;
esac

# ============================================================================
# START – Boot-Sequenz
# ============================================================================
banner

# ── 1. Docker Daemon ────────────────────────────────────────────────────
head "1/6 Docker Daemon"

if ! docker info &>/dev/null 2>&1; then
    echo -e "  Docker Daemon starten (sudo nötig)..."
    sudo systemctl start docker
    sleep 2
    if docker info &>/dev/null 2>&1; then
        ok "Docker Daemon gestartet"
    else
        fail "Docker Daemon konnte nicht gestartet werden"
        exit 1
    fi
else
    ok "Docker Daemon läuft bereits"
fi

# ── 2. Infrastruktur-Container ──────────────────────────────────────────
head "2/6 Infrastruktur (PostgreSQL, Redis, MQTT)"

docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d postgres redis mosquitto 2>&1 | tail -3

# Warte auf Healthy
echo -e "  Warte auf Health-Checks..."
for svc in soma-postgres soma-redis; do
    for i in $(seq 1 30); do
        HEALTH=$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "none")
        if [ "$HEALTH" = "healthy" ]; then
            ok "$svc ist healthy"
            break
        fi
        sleep 1
    done
done
ok "Mosquitto gestartet"

# ── 3. Ollama ───────────────────────────────────────────────────────────
head "3/6 Ollama (LLM Runtime)"

if systemctl is-active --quiet ollama 2>/dev/null; then
    ok "Ollama Service läuft bereits"
elif command -v ollama &>/dev/null; then
    # Prüfe ob Ollama erreichbar
    if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
        ok "Ollama bereits erreichbar"
    else
        warn "Ollama starten: sudo systemctl start ollama"
        sudo systemctl start ollama 2>/dev/null || true
        sleep 3
        if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            ok "Ollama gestartet"
        else
            fail "Ollama konnte nicht gestartet werden"
        fi
    fi
else
    fail "Ollama nicht installiert – LLM-Funktionen deaktiviert"
fi

# Modelle prüfen
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    MODELS=$(curl -sf http://localhost:11434/api/tags | python3 -c "
import sys,json
d=json.load(sys.stdin)
names=[m['name'] for m in d.get('models',[])]
print(', '.join(names) if names else 'keine')
" 2>/dev/null)
    ok "Modelle: $MODELS"
fi

# ── 4. Python venv aktivieren ───────────────────────────────────────────
head "4/6 Python Environment"

VENV="$SCRIPT_DIR/.venv"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
    ok "venv aktiviert: $(python --version)"
else
    fail "venv nicht gefunden! Erst ./init_system.sh ausführen."
    exit 1
fi

# ── 5. Django SSOT ──────────────────────────────────────────────────────
head "5/6 Django SSOT (Port 8200)"

# Prüfe ob schon läuft
if curl -sf http://localhost:8200/ >/dev/null 2>&1; then
    ok "Django läuft bereits"
else
    cd "$SCRIPT_DIR"
    nohup python brain_memory_ui/manage.py runserver 0.0.0.0:8200 \
        > "$SCRIPT_DIR/.logs/django.log" 2>&1 &
    DJANGO_PID=$!
    echo "$DJANGO_PID" > "$PIDDIR/django.pid"
    sleep 2

    if curl -sf http://localhost:8200/ >/dev/null 2>&1; then
        ok "Django gestartet (PID $DJANGO_PID)"
    else
        fail "Django Start fehlgeschlagen – Log: .logs/django.log"
    fi
fi

# ── 6. Brain Core ──────────────────────────────────────────────────────
head "6/6 Brain Core (Port 8100)"

if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
    ok "Brain Core läuft bereits"
else
    cd "$SCRIPT_DIR"
    nohup python -m brain_core.main \
        > "$SCRIPT_DIR/.logs/brain_core.log" 2>&1 &
    BRAIN_PID=$!
    echo "$BRAIN_PID" > "$PIDDIR/brain_core.pid"
    sleep 3

    if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
        ok "Brain Core gestartet (PID $BRAIN_PID) 🧠"
    else
        warn "Brain Core braucht noch einen Moment..."
        sleep 5
        if curl -sf http://localhost:8100/api/v1/health >/dev/null 2>&1; then
            ok "Brain Core gestartet (PID $BRAIN_PID) 🧠"
        else
            fail "Brain Core Start fehlgeschlagen – Log: .logs/brain_core.log"
        fi
    fi
fi

# ============================================================================
# Zusammenfassung
# ============================================================================
echo ""
echo -e "${G}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${G}║${NC}  ${B}SOMA-AI ist online 🧠${NC}                                ${G}║${NC}"
echo -e "${G}╠════════════════════════════════════════════════════════╣${NC}"
echo -e "${G}║${NC}                                                        ${G}║${NC}"
echo -e "${G}║${NC}  Brain Core API:   ${C}http://localhost:8100${NC}              ${G}║${NC}"
echo -e "${G}║${NC}  Django Admin:     ${C}http://localhost:8200/admin/${NC}       ${G}║${NC}"
echo -e "${G}║${NC}  API Docs:         ${C}http://localhost:8100/docs${NC}         ${G}║${NC}"
echo -e "${G}║${NC}  Thinking Stream:  ${C}ws://localhost:8100/ws/thinking${NC}    ${G}║${NC}"
echo -e "${G}║${NC}                                                        ${G}║${NC}"
echo -e "${G}║${NC}  Logs:    ${Y}tail -f .logs/brain_core.log${NC}                ${G}║${NC}"
echo -e "${G}║${NC}  Status:  ${Y}./start_soma.sh --status${NC}                    ${G}║${NC}"
echo -e "${G}║${NC}  Stop:    ${Y}./start_soma.sh --stop${NC}                      ${G}║${NC}"
echo -e "${G}║${NC}                                                        ${G}║${NC}"
echo -e "${G}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
