#!/usr/bin/env bash
# ============================================================================
# SOMA-AI Init System
# Initialisiert Ordnerstruktur, virtuelle Umgebungen und Docker-Services.
# Usage: chmod +x init_system.sh && ./init_system.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ───────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[SOMA]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; }
sep()  { echo -e "${CYAN}────────────────────────────────────────────────────────${NC}"; }

# ============================================================================
echo ""
echo -e "${CYAN}"
echo "  ███████╗ ██████╗ ███╗   ███╗ █████╗ "
echo "  ██╔════╝██╔═══██╗████╗ ████║██╔══██╗"
echo "  ███████╗██║   ██║██╔████╔██║███████║"
echo "  ╚════██║██║   ██║██║╚██╔╝██║██╔══██║"
echo "  ███████║╚██████╔╝██║ ╚═╝ ██║██║  ██║"
echo "  ╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝"
echo -e "${NC}"
echo "  Adaptive Ambient OS – Genesis Init"
echo ""
sep

# ── 1. System-Check ─────────────────────────────────────────────────────
log "Phase 1: System-Prüfung"

check_cmd() {
    if ! command -v "$1" &> /dev/null; then
        err "$1 ist nicht installiert."
        return 1
    fi
    log "  ✓ $1 gefunden: $(command -v "$1")"
}

check_cmd python3 || { err "Python3 benötigt. Install: sudo apt install python3 python3-venv python3-pip"; exit 1; }
check_cmd pip3 || check_cmd pip || warn "pip nicht gefunden – wird über venv bereitgestellt"
check_cmd docker || warn "Docker nicht installiert – Docker-Services werden übersprungen"
check_cmd docker-compose || check_cmd "docker compose" || warn "docker-compose nicht gefunden"

# ── 2. Verzeichnisstruktur verifizieren ──────────────────────────────────
sep
log "Phase 2: Verzeichnisstruktur verifizieren"

DIRS=(
    "shared"
    "brain_core/discovery"
    "brain_core/engines"
    "brain_core/safety"
    "brain_memory_ui/core_settings"
    "brain_memory_ui/hardware"
    "brain_memory_ui/users"
    "brain_memory_ui/dashboard/templates"
    "evolution_lab/generated_plugins"
    "evolution_lab/sandbox_env"
    "evolution_lab/prompts"
    "soma_face_tablet/assets"
    "mosquitto/config"
)

for dir in "${DIRS[@]}"; do
    if [ -d "$dir" ]; then
        log "  ✓ $dir"
    else
        mkdir -p "$dir"
        log "  + $dir (erstellt)"
    fi
done

# ── 3. Python Virtual Environment ───────────────────────────────────────
sep
log "Phase 3: Python Virtual Environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [ -d "$VENV_DIR" ]; then
    log "  venv existiert bereits: $VENV_DIR"
else
    log "  Erstelle venv..."
    python3 -m venv "$VENV_DIR"
    log "  ✓ venv erstellt"
fi

# Aktivieren
source "$VENV_DIR/bin/activate"
log "  ✓ venv aktiviert: $(which python)"

# Dependencies installieren
if [ -f "requirements.txt" ]; then
    log "  Installiere Dependencies..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    log "  ✓ Dependencies installiert"
else
    warn "  requirements.txt nicht gefunden"
fi

# ── 4. .env Datei prüfen ────────────────────────────────────────────────
sep
log "Phase 4: Environment-Konfiguration"

if [ -f ".env" ]; then
    log "  ✓ .env vorhanden"
    # Warnung bei Default-Passwörtern
    if grep -q "soma_secret_change_me" .env; then
        warn "  ⚠ Standard-Passwörter erkannt! Bitte vor Production ändern."
    fi
else
    err "  .env fehlt!"
    exit 1
fi

# ── 5. Docker Services ──────────────────────────────────────────────────
sep
log "Phase 5: Docker-Services"

if command -v docker &> /dev/null; then
    if docker info &> /dev/null; then
        log "  Docker ist erreichbar"

        # Mosquitto Config prüfen
        if [ ! -f "mosquitto/config/mosquitto.conf" ]; then
            warn "  mosquitto.conf fehlt – erstelle Default..."
            cat > mosquitto/config/mosquitto.conf << 'EOF'
listener 1883
protocol mqtt
listener 9001
protocol websockets
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
EOF
        fi

        log "  Starte Docker-Services..."
        if command -v docker-compose &> /dev/null; then
            docker-compose up -d
        else
            docker compose up -d
        fi
        log "  ✓ Docker-Services gestartet"

        # Warte auf PostgreSQL
        log "  Warte auf PostgreSQL..."
        for i in {1..30}; do
            if docker exec soma-postgres pg_isready -U soma &> /dev/null; then
                log "  ✓ PostgreSQL bereit"
                break
            fi
            sleep 1
        done

        # Warte auf Redis
        log "  Warte auf Redis..."
        for i in {1..15}; do
            if docker exec soma-redis redis-cli -a soma_redis_secret ping &> /dev/null; then
                log "  ✓ Redis bereit"
                break
            fi
            sleep 1
        done

    else
        warn "  Docker läuft nicht – Services übersprungen"
    fi
else
    warn "  Docker nicht installiert – Services übersprungen"
fi

# ── 6. Django Migrations ────────────────────────────────────────────────
sep
log "Phase 6: Django SSOT initialisieren"

cd "$SCRIPT_DIR/brain_memory_ui"

if python manage.py check --settings=core_settings.settings 2>/dev/null; then
    log "  Django-Check bestanden"
    python manage.py makemigrations hardware users dashboard --settings=core_settings.settings 2>/dev/null || true
    python manage.py migrate --settings=core_settings.settings 2>/dev/null || warn "  Migration fehlgeschlagen (DB evtl. nicht erreichbar)"
    log "  ✓ Migrations ausgeführt"
else
    warn "  Django-Check fehlgeschlagen – überspringe Migrations"
fi

cd "$SCRIPT_DIR"

# ── 7. Ollama Model Pull ────────────────────────────────────────────────
sep
log "Phase 7: LLM Models"

if command -v docker &> /dev/null && docker ps | grep -q soma-ollama; then
    log "  Ollama Container läuft"
    log "  Lade Llama 3 8B (kann dauern)..."
    docker exec soma-ollama ollama pull llama3:8b 2>/dev/null &
    OLLAMA_PID=$!
    log "  Model-Download läuft im Hintergrund (PID: $OLLAMA_PID)"
else
    warn "  Ollama nicht erreichbar – Model-Download übersprungen"
    warn "  Starte manuell: docker exec soma-ollama ollama pull llama3:8b"
fi

# ── 8. Zusammenfassung ──────────────────────────────────────────────────
sep
echo ""
log "╔══════════════════════════════════════════════════════╗"
log "║         SOMA-AI Genesis Init abgeschlossen          ║"
log "╠══════════════════════════════════════════════════════╣"
log "║                                                      ║"
log "║  Brain Core (FastAPI):                               ║"
log "║    cd $SCRIPT_DIR && source .venv/bin/activate"
log "║    python -m brain_core.main                         ║"
log "║    → http://localhost:8100                            ║"
log "║                                                      ║"
log "║  Brain Memory (Django):                              ║"
log "║    cd brain_memory_ui && python manage.py runserver   ║"
log "║    → http://localhost:8200                            ║"
log "║                                                      ║"
log "║  Tablet Face:                                        ║"
log "║    Öffne soma_face_tablet/index.html                 ║"
log "║                                                      ║"
log "║  Dashboard:                                          ║"
log "║    → http://localhost:8200/admin/                     ║"
log "║                                                      ║"
log "╚══════════════════════════════════════════════════════╝"
echo ""
