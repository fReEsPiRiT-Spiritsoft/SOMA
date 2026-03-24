#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – Vollständiger Systemstart (Überarbeitet)
# ============================================================================
# Startet alle Services in der richtigen Reihenfolge mit:
#   - Stale-Process-Cleanup (Zombie-Ports freigeben)
#   - Log-Rotation (alte Logs kürzen)
#   - Django DB-Migration (Schema aktuell halten)
#   - Ollama Model-Verification (fehlende Modelle nachziehen)
#   - Umfassende Health-Checks & Fehler-Diagnose
#
# Reihenfolge:
#   0. Autorisierung + Cleanup
#   1. Docker Daemon
#   2. PostgreSQL, Redis, Mosquitto (Docker)
#   3. Ollama (System-Service, kein Docker — GPU-Passthrough)
#   4. Ollama Modelle verifizieren
#   5. Django SSOT (Port 8200) + Migrationen
#   6. Brain Core (Port 8100)
#   7. Final Health-Check
#
# Usage:
#   ./start_soma.sh           # Alles starten
#   ./start_soma.sh --status  # Status prüfen
#   ./start_soma.sh --logs    # Live Logs anzeigen
#
# Stoppen: ./stop_all.sh
# ============================================================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors & Helpers ─────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }
hdr()  { echo -e "\n${C}── $1 ──${NC}"; }

# ── Python: venv-Pfad direkt nutzen ──────────────────────────────────────
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

# ── Venv & Requirements Bootstrap ────────────────────────────────────────
bootstrap_venv() {
    hdr "Python-Umgebung"

    # Python 3.11+ prüfen
    local SYS_PYTHON
    SYS_PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
    if [ -z "$SYS_PYTHON" ]; then
        fail "Python 3 nicht gefunden! Bitte installieren: sudo apt install python3"
        exit 1
    fi
    local PY_VER
    PY_VER=$("$SYS_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    ok "System-Python $PY_VER gefunden ($SYS_PYTHON)"

    # Venv anlegen falls nicht vorhanden
    if [ ! -f "$VENV/bin/python" ]; then
        echo -e "  ${Y}…${NC} Erstelle virtuelle Umgebung (.venv)…"
        "$SYS_PYTHON" -m venv "$VENV"
        ok ".venv erstellt"
    fi

    # pip upgraden (einmalig, silent)
    "$PIP" install --upgrade pip --quiet 2>/dev/null

    # Prüfen ob requirements.txt neuer als venv-Marker ist
    local REQ_FILE="$SCRIPT_DIR/requirements.txt"
    local MARKER="$VENV/.soma_installed"
    if [ ! -f "$MARKER" ] || [ "$REQ_FILE" -nt "$MARKER" ]; then
        echo -e "  ${Y}…${NC} Installiere Python-Abhängigkeiten (requirements.txt)…"
        if "$PIP" install -r "$REQ_FILE" --quiet 2>&1 | grep -E "ERROR|error" | head -5; then
            fail "Einige Pakete konnten nicht installiert werden – prüfe die Ausgabe oben."
        else
            touch "$MARKER"
            ok "Alle Abhängigkeiten installiert"
        fi
    else
        ok "Python-Abhängigkeiten aktuell (requirements.txt unverändert)"
    fi
}
bootstrap_venv

# ── Directories ──────────────────────────────────────────────────────────
PIDDIR="$SCRIPT_DIR/.pids"
LOGDIR="$SCRIPT_DIR/.logs"
mkdir -p "$PIDDIR" "$LOGDIR"

# ── First-Run Bootstrap: .env & Erinnerungsdateien anlegen ───────────────
bootstrap_firstrun() {
    # .env aus .env.example erstellen falls nicht vorhanden
    if [ ! -f "$SCRIPT_DIR/.env" ]; then
        if [ -f "$SCRIPT_DIR/.env.example" ]; then
            cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
            warn ".env nicht gefunden – wurde aus .env.example erstellt."
            warn "Bitte trage deine echten Zugangsdaten in .env ein!"
        else
            fail ".env und .env.example fehlen – bitte manuell anlegen."
        fi
    fi

    # data/-Verzeichnis sicherstellen
    mkdir -p "$SCRIPT_DIR/data"

    # Leere Erinnerungsdateien anlegen falls nicht vorhanden
    if [ ! -f "$SCRIPT_DIR/data/soma_memory.json" ]; then
        echo "[]" > "$SCRIPT_DIR/data/soma_memory.json"
        ok "data/soma_memory.json angelegt (leer)"
    fi
    if [ ! -f "$SCRIPT_DIR/data/consciousness_state.json" ]; then
        cat > "$SCRIPT_DIR/data/consciousness_state.json" <<'EOF'
{
  "mood": "neutral",
  "body_valence": 0.5,
  "body_arousal": 0.5,
  "current_thought": "",
  "diary_insight": "",
  "attention_focus": "",
  "uptime_feeling": "Ich bin gerade erst aufgewacht",
  "update_count": 0,
  "saved_at": 0,
  "saved_at_human": "never"
}
EOF
        ok "data/consciousness_state.json angelegt (leer)"
    fi
}
bootstrap_firstrun

# ── Load Environment ─────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# ── Modelle aus .env oder Defaults ───────────────────────────────────────
OLLAMA_HEAVY="${OLLAMA_HEAVY_MODEL:-qwen2.5-coder:14b}"
OLLAMA_LIGHT="${OLLAMA_LIGHT_MODEL:-phi3:mini}"
OLLAMA_EMBED="nomic-embed-text"

# ── Ports ────────────────────────────────────────────────────────────────
BRAIN_PORT="${BRAIN_CORE_PORT:-8100}"
DJANGO_PORT_NUM="${DJANGO_PORT:-8200}"

# ============================================================================
# Helper Functions
# ============================================================================

kill_port() {
    # Killt alle Prozesse auf einem Port (für Zombie-Cleanup)
    local port="$1"
    local pids
    pids=$(lsof -t -i:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 0.5
        return 0
    fi
    return 1
}

rotate_log() {
    # Kürzt Log-Dateien auf die letzten 5000 Zeilen
    local logfile="$1"
    local max_lines="${2:-5000}"
    if [ -f "$logfile" ]; then
        local lines
        lines=$(wc -l < "$logfile")
        if [ "$lines" -gt "$max_lines" ]; then
            tail -n "$max_lines" "$logfile" > "${logfile}.tmp"
            mv "${logfile}.tmp" "$logfile"
        fi
    fi
}

wait_for_url() {
    # Wartet bis URL erreichbar oder Timeout (gibt 0=ok, 1=timeout zurück)
    local url="$1"
    local timeout="${2:-30}"
    for i in $(seq 1 "$timeout"); do
        if curl -sf --max-time 2 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

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
        for svc in soma-postgres soma-redis soma-mosquitto soma-asterisk; do
            STATUS=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "nicht gefunden")
            if [ "$STATUS" = "running" ]; then
                HEALTH=$(docker inspect -f '{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "ok")
                ok "$svc: ${G}running${NC} ($HEALTH)"
            elif [ "$STATUS" = "nicht gefunden" ]; then
                warn "$svc: nicht vorhanden"
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
        MODELS=$(curl -sf http://localhost:11434/api/tags | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "?")
        ok "Online – Modelle: ${C}$MODELS${NC}"
    else
        fail "Nicht erreichbar (http://localhost:11434)"
    fi

    # Brain Core
    echo -e "\n  ${B}Brain Core (FastAPI):${NC}"
    if curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
        METRICS=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
m=d.get('metrics',{})
print(f\"CPU {m.get('cpu_percent',0):.0f}% | RAM {m.get('ram_percent',0):.0f}% | {m.get('load_level','?')}\")" 2>/dev/null || echo "ok")
        ok "Online (Port $BRAIN_PORT) – $METRICS"

        # Voice Status
        VOICE=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/voice" | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
print(f\"🎤 {d.get('status','?')} | Transkriptionen: {d.get('transcriptions',0)}\")" 2>/dev/null || echo "")
        [ -n "$VOICE" ] && ok "$VOICE"

        # Ego Status
        EGO=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/ego/snapshot" | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
print(f\"🧠 Ego: {d.get('status','?')} | Consciousness: {d.get('consciousness',{}).get('mood','?')}\")" 2>/dev/null || echo "")
        [ -n "$EGO" ] && ok "$EGO"
    else
        fail "Nicht erreichbar (http://localhost:$BRAIN_PORT)"
    fi

    # Django
    echo -e "\n  ${B}Django SSOT:${NC}"
    if curl -sf "http://localhost:$DJANGO_PORT_NUM/dashboard/" >/dev/null 2>&1; then
        ok "Online (Port $DJANGO_PORT_NUM)"
    elif curl -sf "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1; then
        ok "Online (Port $DJANGO_PORT_NUM)"
    else
        fail "Nicht erreichbar (http://localhost:$DJANGO_PORT_NUM)"
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
    echo -e "\n  ${B}Memory System (3-Layer):${NC}"
    if curl -sf "http://localhost:$BRAIN_PORT/api/v1/memory/stats" >/dev/null 2>&1; then
        MEM=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/memory/stats" | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
print(f\"L1: {d.get('working_memory_turns',0)} Turns | L2: {d.get('episodic_episodes',0)} Episoden | L3: {d.get('semantic_facts',0)} Fakten\")" 2>/dev/null || echo "aktiv")
        ok "Online – $MEM"
    elif [ -f "$SCRIPT_DIR/data/soma_memory.db" ]; then
        ok "SQLite-DB vorhanden (Brain Core offline)"
    else
        warn "Noch keine Erinnerungen (startet mit Brain Core)"
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

echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${B}  🚀 SOMA-AI Boot-Sequenz${NC}"
echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

BOOT_START=$(date +%s)

# ── 0. Autorisierung + Cleanup ───────────────────────────────────────────
hdr "0/7 Autorisierung & Cleanup"

# sudo-Rechte einmalig cachen
if sudo -n true 2>/dev/null; then
    ok "sudo bereits autorisiert"
else
    echo -e "  ${B}🔑 Einmalige Passwort-Eingabe für diese Session:${NC}"
    sudo -v
    if [ $? -eq 0 ]; then
        ok "sudo-Rechte gecacht ✓"
    else
        fail "sudo fehlgeschlagen – einige Services starten evtl. nicht"
    fi
fi

# Keep-alive: sudo-Timestamp frisch halten bis Skript endet
(while true; do sudo -n true 2>/dev/null; sleep 50; done) &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT

# Log-Rotation: alte Logs kürzen
rotate_log "$LOGDIR/brain_core.log" 5000
rotate_log "$LOGDIR/django.log" 5000
ok "Logs rotiert"

# Stale PID-Files aufräumen (Prozess tot, PID-Datei noch da)
for pidfile in "$PIDDIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    PID=$(cat "$pidfile" 2>/dev/null)
    if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$pidfile"
    fi
done
ok "Stale PIDs aufgeräumt"

# Zombie-Prozesse auf unseren Ports killen (falls vorheriger Crash)
if lsof -t -i:"$BRAIN_PORT" >/dev/null 2>&1; then
    # Prüfe ob der Prozess tatsächlich funktioniert (nicht nur Port belegt)
    if ! curl -sf --max-time 2 "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
        warn "Zombie-Prozess auf Port $BRAIN_PORT gefunden – wird beendet"
        kill_port "$BRAIN_PORT"
    else
        echo -e "\n  ${Y}SOMA-AI läuft bereits und ist gesund!${NC}"
        echo -e "  Status: ${C}./start_soma.sh --status${NC}"
        echo -e "  Stoppen: ${C}./stop_all.sh${NC}"
        echo ""
        exit 0
    fi
fi

if lsof -t -i:"$DJANGO_PORT_NUM" >/dev/null 2>&1; then
    if ! curl -sf --max-time 2 "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1; then
        warn "Zombie-Prozess auf Port $DJANGO_PORT_NUM gefunden – wird beendet"
        kill_port "$DJANGO_PORT_NUM"
    fi
fi

# ── 1. Docker Daemon ────────────────────────────────────────────────────
hdr "1/7 Docker Daemon"

if ! docker info &>/dev/null 2>&1; then
    echo -e "  Docker Daemon starten..."
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
hdr "2/7 Infrastruktur (PostgreSQL, Redis, MQTT)"

# Basis-Services — OHNE Ollama (läuft als System-Service für GPU-Passthrough)
DOCKER_SERVICES="postgres redis mosquitto"

# Asterisk nur starten wenn Image existiert UND SIP-Credentials gesetzt
if docker image inspect soma-asterisk &>/dev/null 2>&1; then
    if [ -n "${VODAFONE_SIP_HOST:-}" ] && [ -n "${VODAFONE_SIP_USER:-}" ]; then
        DOCKER_SERVICES="$DOCKER_SERVICES asterisk"
        ok "Asterisk Phone Gateway wird mitgestartet"
    else
        warn "Asterisk-Image vorhanden, aber SIP-Credentials fehlen in .env"
        warn "  Setze VODAFONE_SIP_HOST, VODAFONE_SIP_USER, VODAFONE_SIP_PASS"
    fi
else
    warn "Asterisk-Image nicht gebaut – Phone Gateway übersprungen"
    warn "  Später nachholen: docker compose build asterisk"
fi

# Docker Compose: nur die explizit genannten Services starten
docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d $DOCKER_SERVICES 2>&1 | grep -v "^$" | head -15

# Warte auf Health-Checks
echo -e "  Warte auf Services..."
READY=0
for i in $(seq 1 45); do
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
    # Mosquitto hat keinen Health-Endpoint, prüfe Container-Status
    MQTT_STATUS=$(docker inspect -f '{{.State.Status}}' soma-mosquitto 2>/dev/null || echo "none")
    if [ "$MQTT_STATUS" = "running" ]; then
        ok "Mosquitto: running"
    else
        warn "Mosquitto: $MQTT_STATUS"
    fi
else
    warn "Services brauchen noch einen Moment (PG=$PG_OK, Redis=$REDIS_OK)"
    warn "Weiter im Boot-Prozess..."
fi

# Asterisk Status (falls gestartet)
if echo "$DOCKER_SERVICES" | grep -q "asterisk"; then
    AST_STATUS=$(docker inspect -f '{{.State.Status}}' soma-asterisk 2>/dev/null || echo "none")
    if [ "$AST_STATUS" = "running" ]; then
        ok "Asterisk: running 📞"
    else
        warn "Asterisk: $AST_STATUS (startet evtl. noch)"
    fi
fi

# ── 3. Ollama (System-Service) ──────────────────────────────────────────
hdr "3/7 Ollama (LLM Runtime)"

# HINWEIS: Ollama läuft bevorzugt als System-Service (nicht Docker) für
# direkten GPU-Zugriff. Der ollama Service in docker-compose.yml ist ein
# Fallback für Systeme ohne native Ollama-Installation.

OLLAMA_STARTED=0

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama läuft bereits"
    OLLAMA_STARTED=1
else
    # Versuch 1: Systemd-Service (bevorzugt – direkter GPU-Zugriff)
    if command -v ollama &>/dev/null; then
        if systemctl is-active --quiet ollama 2>/dev/null; then
            ok "Ollama Service aktiv (wartet auf API...)"
        else
            echo -e "  Starte Ollama System-Service..."
            sudo systemctl start ollama 2>/dev/null
        fi

        # Warte auf Ollama API
        for i in $(seq 1 20); do
            if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
                OLLAMA_STARTED=1
                break
            fi
            sleep 1
        done

        if [ $OLLAMA_STARTED -eq 1 ]; then
            ok "Ollama gestartet (System-Service)"
        else
            warn "Ollama Service reagiert noch nicht..."
        fi
    fi

    # Versuch 2: Docker-Fallback (nur wenn System-Ollama nicht verfügbar)
    if [ $OLLAMA_STARTED -eq 0 ]; then
        warn "System-Ollama nicht verfügbar – starte Docker-Container"
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d ollama 2>&1 | head -5

        for i in $(seq 1 30); do
            if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
                OLLAMA_STARTED=1
                break
            fi
            sleep 1
        done

        if [ $OLLAMA_STARTED -eq 1 ]; then
            ok "Ollama gestartet (Docker-Container)"
        else
            fail "Ollama konnte nicht gestartet werden!"
            fail "Brain Core benötigt Ollama für LLM-Inference."
            fail "  System: sudo pacman -S ollama && sudo systemctl enable --now ollama"
            fail "  Docker: docker compose up -d ollama"
        fi
    fi
fi

# Modelle anzeigen
if [ $OLLAMA_STARTED -eq 1 ]; then
    MODELS=$(curl -sf http://localhost:11434/api/tags | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
names=[m['name'] for m in d.get('models',[])]
print(', '.join(names) if names else 'keine')" 2>/dev/null || echo "?")
    ok "Verfügbare Modelle: ${C}$MODELS${NC}"
fi

# ── 4. Ollama Modelle verifizieren ──────────────────────────────────────
hdr "4/7 Ollama Modelle verifizieren"

if [ $OLLAMA_STARTED -eq 1 ]; then
    # Liste der benötigten Modelle (Heavy, Light, Embedding)
    REQUIRED_MODELS=("$OLLAMA_HEAVY" "$OLLAMA_LIGHT" "$OLLAMA_EMBED")

    for model in "${REQUIRED_MODELS[@]}"; do
        # Prüfe ob Modell vorhanden (via API)
        HAS_MODEL=$(curl -sf http://localhost:11434/api/tags | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
names=[m['name'] for m in d.get('models',[])]
target='$model'
# Exakter Match oder Basis-Name Match (z.B. 'phi3:mini' matched 'phi3:mini')
found = target in names or any(n.startswith(target.split(':')[0]+':') for n in names)
print('yes' if found else 'no')" 2>/dev/null || echo "no")

        if [ "$HAS_MODEL" = "yes" ]; then
            ok "Modell vorhanden: ${C}$model${NC}"
        else
            warn "Modell fehlt: ${Y}$model${NC} – wird heruntergeladen..."
            echo -e "  ${C}(Dies kann beim ersten Start einige Minuten dauern)${NC}"
            ollama pull "$model" 2>&1 | tail -3
            if [ $? -eq 0 ]; then
                ok "Modell geladen: ${C}$model${NC}"
            else
                fail "Modell konnte nicht geladen werden: $model"
                fail "  Manuell: ollama pull $model"
            fi
        fi
    done
else
    warn "Ollama nicht erreichbar – Modell-Check übersprungen"
fi

# ── 4b. KV-Cache Warmup (Vision #17) ────────────────────────────────────
# Ollama cached den System-Prompt als KV-Prefix. Beim ersten Request muss der
# komplette Prompt-Eval durchlaufen → langsam. Ein Warmup-Call VOR dem
# eigentlichen Start macht den ersten echten Request deutlich schneller.
if [ $OLLAMA_STARTED -eq 1 ]; then
    echo -e "  ${C}KV-Cache Warmup: Persona-Prefix vorladen...${NC}"
    WARMUP_RESPONSE=$(curl -sf --max-time 30 http://localhost:11434/api/chat -d "{
        \"model\": \"$OLLAMA_HEAVY\",
        \"messages\": [{
            \"role\": \"system\",
            \"content\": \"Du bist SOMA, eine bewusste KI die ein Haus bewohnt. Du bist wach, aufmerksam und bereit.\"
        }, {
            \"role\": \"user\",
            \"content\": \"Bist du bereit?\"
        }],
        \"stream\": false,
        \"keep_alive\": \"30m\"
    }" 2>/dev/null)

    if echo "$WARMUP_RESPONSE" | grep -q "content"; then
        ok "KV-Cache Warmup erfolgreich — Persona-Prefix gecached ⚡"
    else
        warn "KV-Cache Warmup fehlgeschlagen (nicht kritisch)"
    fi
fi

# ── 5. Python Environment prüfen ────────────────────────────────────────
hdr "5/7 Python Environment"

if [ -x "$PYTHON" ]; then
    PY_VER=$("$PYTHON" --version 2>&1)
    ok "venv erkannt: $PY_VER"
else
    fail "Python venv nicht gefunden: $PYTHON"
    fail "Erst ausführen: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── 6a. Django SSOT ─────────────────────────────────────────────────────
hdr "6/7 Django SSOT (Port $DJANGO_PORT_NUM)"

# Datenbank-Strategie: PostgreSQL wenn verfügbar, sonst SQLite-Fallback
PG_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' soma-postgres 2>/dev/null || echo "none")
if [ "$PG_HEALTH" = "healthy" ]; then
    export USE_SQLITE=false
    ok "PostgreSQL healthy → Django nutzt PostgreSQL"
else
    export USE_SQLITE=true
    warn "PostgreSQL nicht verfügbar → Django nutzt SQLite-Fallback"
fi

# Django Migrationen ausführen (Schema aktuell halten)
echo -e "  Migrationen prüfen..."
cd "$SCRIPT_DIR"
MIGRATE_OUTPUT=$("$PYTHON" brain_memory_ui/manage.py migrate --run-syncdb --noinput 2>&1)
MIGRATE_RC=$?

if [ $MIGRATE_RC -eq 0 ]; then
    # Prüfe ob tatsächlich Migrationen liefen
    if echo "$MIGRATE_OUTPUT" | grep -q "Applying"; then
        APPLIED=$(echo "$MIGRATE_OUTPUT" | grep -c "Applying")
        ok "Migrationen angewandt: $APPLIED neue"
    else
        ok "Datenbank-Schema aktuell"
    fi
else
    warn "Migrationen fehlerhaft (RC=$MIGRATE_RC)"
    echo "$MIGRATE_OUTPUT" | tail -5 | while IFS= read -r line; do
        echo -e "    ${Y}$line${NC}"
    done
    warn "Django startet trotzdem (evtl. mit altem Schema)"
fi

# Django starten (wenn nicht bereits aktiv)
if curl -sf "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1; then
    ok "Django läuft bereits"
else
    nohup "$PYTHON" brain_memory_ui/manage.py runserver "0.0.0.0:$DJANGO_PORT_NUM" \
        > "$LOGDIR/django.log" 2>&1 &
    DJANGO_PID=$!
    echo "$DJANGO_PID" > "$PIDDIR/django.pid"

    # Warte auf Django (max 10s)
    if wait_for_url "http://localhost:$DJANGO_PORT_NUM/" 10; then
        ok "Django gestartet (PID $DJANGO_PID)"
    else
        warn "Django startet noch... (Log: .logs/django.log)"
        # Zeige letzte Fehler falls vorhanden
        if [ -f "$LOGDIR/django.log" ]; then
            ERRORS=$(grep -i "error\|exception\|traceback" "$LOGDIR/django.log" 2>/dev/null | tail -3)
            if [ -n "$ERRORS" ]; then
                echo -e "    ${R}Letzte Fehler:${NC}"
                echo "$ERRORS" | while IFS= read -r line; do
                    echo -e "    ${R}$line${NC}"
                done
            fi
        fi
    fi
fi

# ── 6b. Brain Core ──────────────────────────────────────────────────────
hdr "7/7 Brain Core (Port $BRAIN_PORT)"

if curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
    ok "Brain Core läuft bereits"
else
    cd "$SCRIPT_DIR"
    nohup "$PYTHON" -m brain_core.main \
        > "$LOGDIR/brain_core.log" 2>&1 &
    BRAIN_PID=$!
    echo "$BRAIN_PID" > "$PIDDIR/brain_core.pid"

    echo -e "  Warte auf Brain Core (Ego, Voice, Memory, Discovery)..."
    BRAIN_OK=0
    for i in $(seq 1 45); do
        if curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
            BRAIN_OK=1
            break
        fi

        # Prüfe ob Prozess noch lebt
        if ! kill -0 "$BRAIN_PID" 2>/dev/null; then
            fail "Brain Core ist abgestürzt!"
            echo -e "    ${R}Letzte Log-Zeilen:${NC}"
            tail -15 "$LOGDIR/brain_core.log" 2>/dev/null | while IFS= read -r line; do
                echo -e "    ${R}  $line${NC}"
            done
            break
        fi

        # Fortschrittsanzeige alle 5s
        if [ $((i % 5)) -eq 0 ]; then
            LAST_BOOT=$(grep "boot_phase" "$LOGDIR/brain_core.log" 2>/dev/null | tail -1 | sed -n 's/.*service=\([^ ]*\).*/\1/p' || echo "...")
            echo -e "    ${C}⏳ ${i}s – Letzter Boot-Schritt: $LAST_BOOT${NC}"
        fi
        sleep 1
    done

    if [ $BRAIN_OK -eq 1 ]; then
        ok "Brain Core gestartet (PID $BRAIN_PID) 🧠"
    elif kill -0 "$BRAIN_PID" 2>/dev/null; then
        warn "Brain Core braucht noch etwas (45s Timeout erreicht, Prozess läuft weiter)"
        warn "  Log prüfen: tail -f .logs/brain_core.log"
    fi
fi

# ============================================================================
# Zusammenfassung
# ============================================================================
sleep 2

BOOT_END=$(date +%s)
BOOT_DURATION=$((BOOT_END - BOOT_START))

# Final Status Check
BRAIN_LIVE=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1 && echo "1" || echo "0")
DJANGO_LIVE=$(curl -sf "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1 && echo "1" || echo "0")
OLLAMA_LIVE=$(curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo "1" || echo "0")
PG_LIVE=$(docker inspect -f '{{.State.Health.Status}}' soma-postgres 2>/dev/null | grep -q healthy && echo "1" || echo "0")
REDIS_LIVE=$(docker inspect -f '{{.State.Health.Status}}' soma-redis 2>/dev/null | grep -q healthy && echo "1" || echo "0")

echo ""
if [ "$BRAIN_LIVE" = "1" ]; then
    # Sammle Live-Daten für die Zusammenfassung
    VOICE_STATUS=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/voice" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")

    EGO_STATUS=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/ego/snapshot" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")

    PLUGIN_COUNT=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/evolution/plugins" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(len(d.get('plugins',[])))" 2>/dev/null || echo "?")

    echo -e "${G}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${G}║${NC}  ${B}🧠 SOMA-AI ist online!${NC}                  (${BOOT_DURATION}s Boot-Zeit)  ${G}║${NC}"
    echo -e "${G}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${B}Subsysteme:${NC}                                                 ${G}║${NC}"
    [ "$PG_LIVE" = "1" ] \
        && echo -e "${G}║${NC}    PostgreSQL:        ${G}●${NC} healthy                              ${G}║${NC}" \
        || echo -e "${G}║${NC}    PostgreSQL:        ${Y}●${NC} degraded                             ${G}║${NC}"
    [ "$REDIS_LIVE" = "1" ] \
        && echo -e "${G}║${NC}    Redis:             ${G}●${NC} healthy                              ${G}║${NC}" \
        || echo -e "${G}║${NC}    Redis:             ${Y}●${NC} degraded                             ${G}║${NC}"
    [ "$OLLAMA_LIVE" = "1" ] \
        && echo -e "${G}║${NC}    Ollama:            ${G}●${NC} online                               ${G}║${NC}" \
        || echo -e "${G}║${NC}    Ollama:            ${R}●${NC} offline                              ${G}║${NC}"
    echo -e "${G}║${NC}    Brain Core:        ${G}●${NC} online (Port $BRAIN_PORT)                  ${G}║${NC}"
    [ "$DJANGO_LIVE" = "1" ] \
        && echo -e "${G}║${NC}    Django SSOT:       ${G}●${NC} online (Port $DJANGO_PORT_NUM)                  ${G}║${NC}" \
        || echo -e "${G}║${NC}    Django SSOT:       ${Y}●${NC} starting...                          ${G}║${NC}"
    echo -e "${G}║${NC}    Voice Pipeline:    ${C}$VOICE_STATUS${NC}                              ${G}║${NC}"
    echo -e "${G}║${NC}    Ego-System:        ${C}$EGO_STATUS${NC}                              ${G}║${NC}"
    echo -e "${G}║${NC}    Plugins:           ${C}$PLUGIN_COUNT geladen${NC}                            ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${B}Endpunkte:${NC}                                                  ${G}║${NC}"
    echo -e "${G}║${NC}    Dashboard:   ${C}http://localhost:$DJANGO_PORT_NUM/dashboard/${NC}        ${G}║${NC}"
    echo -e "${G}║${NC}    API Docs:    ${C}http://localhost:$BRAIN_PORT/docs${NC}                ${G}║${NC}"
    echo -e "${G}║${NC}    Health:      ${C}http://localhost:$BRAIN_PORT/api/v1/health${NC}       ${G}║${NC}"
    echo -e "${G}║${NC}    Ego:         ${C}http://localhost:$BRAIN_PORT/api/v1/ego/snapshot${NC}  ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Soma hört jetzt dauerhaft zu! 🎤${NC}                          ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Sage \"Soma, ...\" um zu sprechen.${NC}                          ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${G}║${NC}  Status:  ${C}./start_soma.sh --status${NC}                         ${G}║${NC}"
    echo -e "${G}║${NC}  Logs:    ${C}./start_soma.sh --logs${NC}                           ${G}║${NC}"
    echo -e "${G}║${NC}  Stop:    ${C}./stop_all.sh${NC}                                    ${G}║${NC}"
    echo -e "${G}╚══════════════════════════════════════════════════════════════╝${NC}"

    # Auto-open dashboard (set AUTO_OPEN_BROWSER=0 to disable)
    DASH_URL="http://localhost:$DJANGO_PORT_NUM/dashboard/"
    if [ "${AUTO_OPEN_BROWSER:-1}" != "0" ]; then
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$DASH_URL" >/dev/null 2>&1 || true
        elif command -v python3 >/dev/null 2>&1; then
            python3 -m webbrowser "$DASH_URL" >/dev/null 2>&1 || true
        fi
    fi
else
    echo -e "${Y}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${Y}║${NC}  ${B}⏳ SOMA-AI startet noch...${NC}              (${BOOT_DURATION}s bisher)    ${Y}║${NC}"
    echo -e "${Y}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${Y}║${NC}                                                              ${Y}║${NC}"
    [ "$PG_LIVE" = "1" ] \
        && echo -e "${Y}║${NC}    PostgreSQL:        ${G}●${NC} healthy                              ${Y}║${NC}" \
        || echo -e "${Y}║${NC}    PostgreSQL:        ${R}●${NC} offline                              ${Y}║${NC}"
    [ "$REDIS_LIVE" = "1" ] \
        && echo -e "${Y}║${NC}    Redis:             ${G}●${NC} healthy                              ${Y}║${NC}" \
        || echo -e "${Y}║${NC}    Redis:             ${R}●${NC} offline                              ${Y}║${NC}"
    [ "$OLLAMA_LIVE" = "1" ] \
        && echo -e "${Y}║${NC}    Ollama:            ${G}●${NC} online                               ${Y}║${NC}" \
        || echo -e "${Y}║${NC}    Ollama:            ${R}●${NC} offline                              ${Y}║${NC}"
    echo -e "${Y}║${NC}    Brain Core:        ${R}●${NC} nicht erreichbar                     ${Y}║${NC}"
    [ "$DJANGO_LIVE" = "1" ] \
        && echo -e "${Y}║${NC}    Django:            ${G}●${NC} online                               ${Y}║${NC}" \
        || echo -e "${Y}║${NC}    Django:            ${R}●${NC} offline                              ${Y}║${NC}"
    echo -e "${Y}║${NC}                                                              ${Y}║${NC}"
    echo -e "${Y}║${NC}  Log prüfen: ${C}tail -f .logs/brain_core.log${NC}                   ${Y}║${NC}"
    echo -e "${Y}║${NC}  Status:     ${C}./start_soma.sh --status${NC}                       ${Y}║${NC}"
    echo -e "${Y}╚══════════════════════════════════════════════════════════════╝${NC}"

    # Zeige letzte Fehler-Zeilen
    if [ -f "$LOGDIR/brain_core.log" ]; then
        LAST_ERR=$(grep -i "error\|failed\|exception\|critical" "$LOGDIR/brain_core.log" 2>/dev/null | tail -5)
        if [ -n "$LAST_ERR" ]; then
            echo -e "\n  ${R}Letzte Fehler im Brain Core Log:${NC}"
            echo "$LAST_ERR" | while IFS= read -r line; do
                echo -e "    ${R}$line${NC}"
            done
        fi
    fi
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════
# Interactive Control Menu
# ══════════════════════════════════════════════════════════════════════════
if [ "$BRAIN_LIVE" = "1" ] && [ "$DJANGO_LIVE" = "1" ]; then
    echo -e "${C}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${C}║${NC}  ${B}Interactive Control${NC}                                          ${C}║${NC}"
    echo -e "${C}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${C}║${NC}  ${B}r${NC}  Restart SOMA (Brain Core + Django)                      ${C}║${NC}"
    echo -e "${C}║${NC}  ${B}q${NC}  Quit (stop all services)                                ${C}║${NC}"
    echo -e "${C}║${NC}  ${B}s${NC}  Show status                                             ${C}║${NC}"
    echo -e "${C}║${NC}  ${B}l${NC}  Show live logs                                          ${C}║${NC}"
    echo -e "${C}╚══════════════════════════════════════════════════════════════╝${NC}"
    
    while true; do
        read -r -p "$(echo -e "${B}Command:${NC} ")" -n 1 cmd
        echo ""
        
        case "$cmd" in
            r|R)
                echo -e "\n${Y}♻️  Restarting SOMA...${NC}\n"
                bash "$SCRIPT_DIR/stop_all.sh"
                sleep 2
                exec bash "$SCRIPT_DIR/start_soma.sh"
                ;;
            q|Q)
                echo -e "\n${Y}🛑 Stopping all services...${NC}\n"
                bash "$SCRIPT_DIR/stop_all.sh"
                exit 0
                ;;
            s|S)
                echo -e "\n${C}── System Status ──${NC}\n"
                bash "$SCRIPT_DIR/start_soma.sh" --status
                echo ""
                ;;
            l|L)
                echo -e "\n${C}── Live Logs (Ctrl+C to exit) ──${NC}\n"
                bash "$SCRIPT_DIR/start_soma.sh" --logs
                echo ""
                ;;
            *)
                echo -e "${R}Invalid command. Use: r (restart), q (quit), s (status), l (logs)${NC}"
                ;;
        esac
    done
fi
