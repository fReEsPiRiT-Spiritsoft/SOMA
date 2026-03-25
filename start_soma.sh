#!/usr/bin/env bash
# ============================================================================
# SOMA-AI – Vollstaendiger Systemstart & Fresh-Install Bootstrap
# ============================================================================
#
# Diese Datei macht ein voellig frisches Linux SOMA-ready:
#   Phase 0: System-Pakete (apt/pacman/dnf)
#   Phase 1: Docker Engine installieren & starten
#   Phase 2: Python venv + pip Dependencies
#   Phase 3: Ollama installieren & Modelle laden
#   Phase 4: .env Konfiguration sicherstellen
#   Phase 5: Mosquitto-Config erstellen
#   Phase 6: Docker-Container (Postgres, Redis, Mosquitto)
#   Phase 7: Ollama Modelle verifizieren + KV-Cache Warmup
#   Phase 8: Django SSOT (Migrationen + Start)
#   Phase 9: Brain Core (FastAPI + Voice + Ego + Memory)
#   Phase 10: Final Health-Check & Summary
#
# Usage:
#   ./start_soma.sh           # Alles installieren & starten
#   ./start_soma.sh --status  # Status pruefen
#   ./start_soma.sh --logs    # Live Logs anzeigen
#
# Stoppen: ./stop_all.sh
# ============================================================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Marker-Datei fuer First-Run ─────────────────────────────────────────
FIRST_RUN_MARKER="$SCRIPT_DIR/.soma_first_run_done"

# ── Colors & Helpers ─────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; B='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${G}✓${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✗${NC} $1"; }
hdr()  { echo -e "\n${C}━━ $1 ━━${NC}"; }

# ── Python: venv-Pfade ──────────────────────────────────────────────────
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

# ── Directories ──────────────────────────────────────────────────────────
PIDDIR="$SCRIPT_DIR/.pids"
LOGDIR="$SCRIPT_DIR/.logs"
mkdir -p "$PIDDIR" "$LOGDIR" "$SCRIPT_DIR/data"

# ── Helper Functions ─────────────────────────────────────────────────────
kill_port() {
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

# ── Distro-Erkennung ────────────────────────────────────────────────────
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        DISTRO_LIKE="${ID_LIKE:-$DISTRO_ID}"
    else
        DISTRO_ID="unknown"
        DISTRO_LIKE="unknown"
    fi

    # Paketmanager bestimmen
    if command -v apt-get &>/dev/null; then
        PKG_MANAGER="apt"
    elif command -v pacman &>/dev/null; then
        PKG_MANAGER="pacman"
    elif command -v dnf &>/dev/null; then
        PKG_MANAGER="dnf"
    elif command -v zypper &>/dev/null; then
        PKG_MANAGER="zypper"
    else
        PKG_MANAGER="unknown"
    fi
}

# ── Paket-Installation abstrahiert ──────────────────────────────────────
install_packages() {
    local description="$1"
    shift
    echo -e "  ${Y}…${NC} $description"
    case "$PKG_MANAGER" in
        apt)
            sudo apt-get install -y -qq "$@" 2>&1 | tail -2
            ;;
        pacman)
            sudo pacman -S --noconfirm --needed "$@" 2>&1 | tail -2
            ;;
        dnf)
            sudo dnf install -y -q "$@" 2>&1 | tail -2
            ;;
        zypper)
            sudo zypper install -y -n "$@" 2>&1 | tail -2
            ;;
        *)
            fail "Unbekannter Paketmanager – bitte manuell installieren: $*"
            return 1
            ;;
    esac
}

# ── Pruefen ob Paket installiert ist ────────────────────────────────────
is_pkg_installed() {
    case "$PKG_MANAGER" in
        apt)    dpkg -s "$1" &>/dev/null ;;
        pacman) pacman -Qi "$1" &>/dev/null ;;
        dnf)    rpm -q "$1" &>/dev/null ;;
        zypper) rpm -q "$1" &>/dev/null ;;
        *)      return 1 ;;
    esac
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
# STATUS
# ============================================================================
show_status() {
    # .env laden falls vorhanden (fuer Port-Variablen)
    if [ -f "$SCRIPT_DIR/.env" ]; then
        set -a; source "$SCRIPT_DIR/.env"; set +a
    fi
    local BRAIN_PORT="${BRAIN_CORE_PORT:-8100}"
    local DJANGO_PORT_NUM="${DJANGO_PORT:-8200}"

    echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${B}  SOMA-AI Systemstatus${NC}"
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

        VOICE=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/voice" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(f\"Mic: {d.get('status','?')} | STT: {d.get('transcriptions',0)}\")" 2>/dev/null || echo "")
        [ -n "$VOICE" ] && ok "$VOICE"

        EGO=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/ego/snapshot" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(f\"Ego: {d.get('status','?')} | Mood: {d.get('consciousness',{}).get('mood','?')}\")" 2>/dev/null || echo "")
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

    # Audio
    echo -e "\n  ${B}Audio Hardware:${NC}"
    if arecord -l 2>/dev/null | grep -q "card"; then
        DEVICE=$(arecord -l 2>/dev/null | grep -i "scarlett\|focusrite\|usb\|card" | head -1)
        ok "Erkannt: $DEVICE"
    else
        warn "Kein Audio-Device gefunden"
    fi

    # Memory
    echo -e "\n  ${B}Memory System:${NC}"
    if curl -sf "http://localhost:$BRAIN_PORT/api/v1/memory/stats" >/dev/null 2>&1; then
        MEM=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/memory/stats" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin)
print(f\"L1: {d.get('working_memory_turns',0)} | L2: {d.get('episodic_episodes',0)} | L3: {d.get('semantic_facts',0)}\")" 2>/dev/null || echo "aktiv")
        ok "$MEM"
    elif [ -f "$SCRIPT_DIR/data/soma_memory.db" ]; then
        ok "SQLite-DB vorhanden (Brain Core offline)"
    else
        warn "Noch keine Erinnerungen"
    fi

    # Plugins
    echo -e "\n  ${B}Evolution Lab:${NC}"
    PLUGINS=$(ls -1 "$SCRIPT_DIR/evolution_lab/generated_plugins/"*.py 2>/dev/null | wc -l || echo "0")
    ok "$PLUGINS Plugins installiert"

    echo -e "\n${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    exit 0
}

# ============================================================================
# LOGS
# ============================================================================
show_logs() {
    echo -e "\n${C}━━━ SOMA-AI Live Logs (Ctrl+C zum Beenden) ━━━${NC}\n"
    tail -f "$LOGDIR/brain_core.log" "$LOGDIR/django.log" 2>/dev/null || \
        tail -f "$LOGDIR/brain_core.log" 2>/dev/null || \
        echo "Keine Logs gefunden. Erst ./start_soma.sh ausfuehren."
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
        echo "  (keine Argumente) = System installieren & starten"
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
echo -e "${B}  SOMA-AI Boot-Sequenz${NC}"
echo -e "${C}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

BOOT_START=$(date +%s)

# ── 0. Autorisierung ────────────────────────────────────────────────────
hdr "Phase 0/10: Autorisierung"

if sudo -n true 2>/dev/null; then
    ok "sudo bereits autorisiert"
else
    echo -e "  ${B}Einmalige Passwort-Eingabe fuer diese Session:${NC}"
    sudo -v
    if [ $? -eq 0 ]; then
        ok "sudo-Rechte gecacht"
    else
        fail "sudo fehlgeschlagen – Installation nicht moeglich"
        exit 1
    fi
fi

# Keep-alive: sudo-Timestamp frisch halten
(while true; do sudo -n true 2>/dev/null; sleep 50; done) &
SUDO_KEEPALIVE_PID=$!
trap "kill $SUDO_KEEPALIVE_PID 2>/dev/null" EXIT

# Distro erkennen
detect_distro
ok "Distro: ${C}${DISTRO_ID}${NC} (Paketmanager: ${C}${PKG_MANAGER}${NC})"

# ============================================================================
# Phase 1: System-Pakete (nur beim ersten Start oder wenn Marker fehlt)
# ============================================================================
hdr "Phase 1/10: System-Pakete"

install_system_packages() {
    echo -e "  ${B}Installiere System-Abhaengigkeiten...${NC}"

    # Paketliste je nach Distro
    case "$PKG_MANAGER" in
        apt)
            sudo apt-get update -qq

            # ── Kritisch (ohne diese startet nichts) ─────────────────────
            local CRITICAL_PKGS=(
                python3 python3-venv python3-pip python3-dev
                build-essential pkg-config
                curl wget git lsof
                alsa-utils
                libsndfile1-dev
                espeak-ng
                ffmpeg
                libffi-dev libssl-dev
                libpq-dev
            )

            # ── Wichtig (fuer volle Funktionalitaet) ─────────────────────
            local IMPORTANT_PKGS=(
                bluez
                avahi-daemon libavahi-compat-libdnssd-dev
                lm-sensors
                portaudio19-dev
            )

            # ── Optional (Desktop-Steuerung, Clipboard etc.) ─────────────
            local OPTIONAL_PKGS=(
                wmctrl xdotool xclip wl-clipboard
                libnotify-bin brightnessctl xdg-utils
            )

            echo -e "  ${Y}…${NC} Kritische Pakete..."
            sudo apt-get install -y -qq "${CRITICAL_PKGS[@]}" 2>&1 | grep -v "is already" | tail -3
            ok "Kritische Pakete installiert"

            echo -e "  ${Y}…${NC} Wichtige Pakete..."
            sudo apt-get install -y -qq "${IMPORTANT_PKGS[@]}" 2>&1 | grep -v "is already" | tail -3
            ok "Wichtige Pakete installiert"

            echo -e "  ${Y}…${NC} Optionale Pakete (Desktop-Steuerung)..."
            sudo apt-get install -y -qq "${OPTIONAL_PKGS[@]}" 2>&1 || warn "Einige optionale Pakete nicht verfuegbar (ok)"
            ok "Optionale Pakete verarbeitet"
            ;;

        pacman)
            sudo pacman -Sy --noconfirm 2>&1 | tail -2

            # Pacman: Pakete einzeln installieren um Konflikte graceful zu handeln
            # (z.B. ffmpeg4.4 vs libvpx Konflikte auf CachyOS)
            local CRITICAL_PKGS=(
                python python-pip
                base-devel pkg-config
                curl wget git lsof
                alsa-utils
                libsndfile
                espeak-ng
                libffi openssl
                postgresql-libs
            )
            local IMPORTANT_PKGS=(
                ffmpeg
                bluez bluez-utils
                avahi nss-mdns
                lm_sensors
                portaudio
                docker docker-compose docker-buildx
            )
            local OPTIONAL_PKGS=(
                wmctrl xdotool xclip wl-clipboard
                libnotify brightnessctl xdg-utils
                python-virtualenv
            )

            echo -e "  ${Y}…${NC} Kritische Pakete..."
            for pkg in "${CRITICAL_PKGS[@]}"; do
                if ! pacman -Qi "$pkg" &>/dev/null; then
                    sudo pacman -S --noconfirm --needed "$pkg" 2>&1 | tail -1 || warn "$pkg konnte nicht installiert werden"
                fi
            done
            ok "Kritische Pakete verarbeitet"

            echo -e "  ${Y}…${NC} Wichtige Pakete..."
            for pkg in "${IMPORTANT_PKGS[@]}"; do
                if ! pacman -Qi "$pkg" &>/dev/null; then
                    sudo pacman -S --noconfirm --needed "$pkg" 2>&1 | tail -1 || warn "$pkg uebersprungen (Konflikt?)"
                fi
            done
            ok "Wichtige Pakete verarbeitet"

            echo -e "  ${Y}…${NC} Optionale Pakete..."
            for pkg in "${OPTIONAL_PKGS[@]}"; do
                sudo pacman -S --noconfirm --needed "$pkg" &>/dev/null || true
            done
            ok "Optionale Pakete verarbeitet"
            ;;

        dnf)
            local PKGS=(
                python3 python3-pip python3-devel python3-virtualenv
                gcc gcc-c++ make pkg-config
                curl wget git lsof
                alsa-utils alsa-lib-devel
                libsndfile-devel
                espeak-ng
                ffmpeg-free
                libffi-devel openssl-devel
                libpq-devel
                bluez
                avahi avahi-compat-libdns_sd-devel
                lm_sensors
                portaudio-devel
                wmctrl xdotool xclip
                libnotify brightnessctl xdg-utils
            )

            sudo dnf install -y -q "${PKGS[@]}" 2>&1 | tail -5
            ok "Pakete installiert"
            ;;

        *)
            fail "Paketmanager '$PKG_MANAGER' wird nicht unterstuetzt."
            fail "Bitte manuell installieren:"
            echo "  python3, python3-venv, python3-dev, build-essential, curl, wget, git,"
            echo "  lsof, alsa-utils, libsndfile, espeak-ng, ffmpeg, libffi, openssl,"
            echo "  libpq-dev, bluez, avahi, lm-sensors, portaudio"
            echo ""
            read -r -p "Weiter trotzdem? (j/N) " ans
            [ "$ans" != "j" ] && [ "$ans" != "J" ] && exit 1
            ;;
    esac
}

# System-Pakete nur installieren wenn Marker fehlt oder --force
if [ ! -f "$FIRST_RUN_MARKER" ]; then
    echo -e "  ${Y}Erster Start erkannt – volle System-Installation${NC}"
    install_system_packages
else
    ok "System-Pakete bereits installiert (Marker vorhanden)"
    # Trotzdem minimale Pruefung
    MISSING=""
    for cmd in python3 curl git docker ffmpeg espeak-ng arecord lsof; do
        if ! command -v "$cmd" &>/dev/null; then
            MISSING="$MISSING $cmd"
        fi
    done
    if [ -n "$MISSING" ]; then
        warn "Fehlende Befehle:${R}$MISSING${NC} – starte Nachinstallation"
        install_system_packages
    fi
fi

# ============================================================================
# Phase 2: Docker installieren
# ============================================================================
hdr "Phase 2/10: Docker Engine"

if ! command -v docker &>/dev/null; then
    echo -e "  ${Y}Docker nicht gefunden – automatische Installation${NC}"

    case "$PKG_MANAGER" in
        apt)
            echo -e "  ${Y}…${NC} Installiere Docker via apt..."
            sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

            # Docker GPG Key
            sudo install -m 0755 -d /etc/apt/keyrings
            if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
                # Fuer Derivate (Mint, Pop) auf Ubuntu/Debian zurueckfallen
                local DOCKER_DISTRO="$DISTRO_ID"
                if [ "$DISTRO_ID" = "linuxmint" ] || [ "$DISTRO_ID" = "pop" ]; then
                    DOCKER_DISTRO="ubuntu"
                fi
                curl -fsSL "https://download.docker.com/linux/${DOCKER_DISTRO}/gpg" \
                    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
                sudo chmod a+r /etc/apt/keyrings/docker.gpg
            fi

            # Docker Repo
            local CODENAME
            CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME" 2>/dev/null || lsb_release -cs)
            # Mint/Pop: upstream Ubuntu codename verwenden
            if [ "$DISTRO_ID" = "linuxmint" ]; then
                CODENAME=$(grep UBUNTU_CODENAME /etc/os-release 2>/dev/null | cut -d= -f2 || echo "$CODENAME")
            fi
            local DOCKER_DISTRO="$DISTRO_ID"
            if [ "$DISTRO_ID" = "linuxmint" ] || [ "$DISTRO_ID" = "pop" ]; then
                DOCKER_DISTRO="ubuntu"
            fi

            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DOCKER_DISTRO} ${CODENAME} stable" \
                | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

            sudo apt-get update -qq
            sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
            ;;
        pacman)
            sudo pacman -S --noconfirm docker docker-compose
            ;;
        dnf)
            sudo dnf install -y -q docker docker-compose-plugin
            ;;
        *)
            fail "Docker bitte manuell installieren: https://docs.docker.com/engine/install/"
            ;;
    esac

    # User zur docker-Gruppe hinzufuegen
    if command -v docker &>/dev/null; then
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        ok "Docker installiert (User '$USER' zur docker-Gruppe hinzugefuegt)"
        warn "Falls Docker-Befehle ohne sudo scheitern: einmal aus- und einloggen"
    else
        fail "Docker-Installation fehlgeschlagen!"
    fi
fi

# Docker Daemon starten
if ! docker info &>/dev/null 2>&1; then
    echo -e "  ${Y}…${NC} Docker Daemon starten..."

    if grep -qi "microsoft\|wsl" /proc/version 2>/dev/null; then
        # WSL: kein systemd → dockerd direkt
        if ! pgrep -x dockerd &>/dev/null; then
            sudo dockerd > /tmp/dockerd.log 2>&1 &
            sleep 3
        fi
    elif command -v systemctl &>/dev/null; then
        # Systemd: Socket UND Service enablen + starten
        # docker.socket ist der bevorzugte Weg (on-demand Aktivierung)
        sudo systemctl enable docker.socket 2>/dev/null || true
        sudo systemctl start docker.socket 2>/dev/null || true
        sudo systemctl enable docker.service 2>/dev/null || true
        sudo systemctl start docker.service 2>/dev/null || true
        sleep 2

        # Warte bis Docker API antwortet (max 10s)
        for i in $(seq 1 10); do
            if docker info &>/dev/null 2>&1 || sudo docker info &>/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
    else
        sudo service docker start 2>/dev/null || true
        sleep 2
    fi

    if docker info &>/dev/null 2>&1; then
        ok "Docker Daemon gestartet"
    else
        # Fallback: mit sudo erreichbar?
        if sudo docker info &>/dev/null 2>&1; then
            warn "Docker laeuft, aber dein User hat noch keine Rechte"
            warn "Fuer diese Session nutze ich sudo fuer Docker-Befehle"
            # docker-Wrapper fuer dieses Skript
            _real_docker=$(command -v docker)
            docker() { sudo "$_real_docker" "$@"; }
            export -f docker
            ok "Docker via sudo erreichbar"
        else
            fail "Docker Daemon konnte nicht gestartet werden!"
            fail "Versuche manuell:"
            fail "  sudo systemctl enable --now docker.socket"
            fail "  sudo systemctl start docker.service"
            warn "Fahre ohne Docker fort (Postgres/Redis/Mosquitto nicht verfuegbar)"
        fi
    fi
else
    ok "Docker Daemon laeuft"
fi

# ============================================================================
# Phase 3: Python Virtual Environment + Dependencies
# ============================================================================
hdr "Phase 3/10: Python-Umgebung"

# Python-Version pruefen
SYS_PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$SYS_PYTHON" ]; then
    fail "Python 3 nicht gefunden!"
    fail "Installation: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi

PY_VER=$("$SYS_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
PY_MAJOR=$("$SYS_PYTHON" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
PY_MINOR=$("$SYS_PYTHON" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python $PY_VER ist zu alt! SOMA benoetigt mindestens Python 3.10"
    fail "Installiere Python 3.11+: sudo apt install python3.11 python3.11-venv"
    exit 1
fi
ok "System-Python $PY_VER ($SYS_PYTHON)"

# venv pruefen: existiert, hat pip, richtige Python-Version?
VENV_REBUILD=0

if [ ! -f "$VENV/bin/python" ]; then
    VENV_REBUILD=1
    echo -e "  ${Y}…${NC} Kein venv gefunden"
elif [ ! -f "$VENV/bin/pip" ] && [ ! -f "$VENV/bin/pip3" ]; then
    VENV_REBUILD=1
    warn "venv existiert aber pip fehlt (evtl. uv-basiert oder beschaedigt)"
else
    # Pruefen ob venv-Python noch existiert und zur System-Version passt
    VENV_PY_VER=$("$VENV/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "broken")
    if [ "$VENV_PY_VER" = "broken" ]; then
        VENV_REBUILD=1
        warn "venv-Python ist nicht mehr ausfuehrbar"
    elif [ "$VENV_PY_VER" != "$PY_VER" ]; then
        warn "venv nutzt Python $VENV_PY_VER, System hat $PY_VER"
        # Nur rebuilden wenn System neuer ist
        VENV_REBUILD=1
    fi
fi

if [ $VENV_REBUILD -eq 1 ]; then
    echo -e "  ${Y}…${NC} Erstelle virtuelle Umgebung (.venv) mit Python $PY_VER..."
    # Altes venv komplett entfernen (sauberer Neuanfang)
    if [ -d "$VENV" ]; then
        rm -rf "$VENV"
        ok "Altes venv entfernt"
    fi
    "$SYS_PYTHON" -m venv "$VENV" || {
        fail "venv-Erstellung fehlgeschlagen!"
        case "$PKG_MANAGER" in
            apt) fail "Installiere: sudo apt install python3-venv python3-dev" ;;
            pacman) fail "Installiere: sudo pacman -S python python-pip" ;;
            dnf) fail "Installiere: sudo dnf install python3-devel python3-virtualenv" ;;
        esac
        exit 1
    }
    ok ".venv erstellt (Python $PY_VER)"

    # Sicherstellen dass pip vorhanden ist
    if [ ! -f "$VENV/bin/pip" ] && [ ! -f "$VENV/bin/pip3" ]; then
        echo -e "  ${Y}…${NC} pip via ensurepip bootstrappen..."
        "$VENV/bin/python" -m ensurepip --upgrade 2>&1 | tail -3 || {
            fail "pip konnte nicht installiert werden!"
            exit 1
        }
    fi

    # Marker entfernen damit requirements neu installiert werden
    rm -f "$VENV/.soma_installed"
else
    ok "venv intakt (Python $VENV_PY_VER)"
fi

# pip upgraden
if [ -f "$PIP" ]; then
    "$PIP" install --upgrade pip --quiet 2>/dev/null
    ok "pip aktuell"
elif [ -f "$VENV/bin/pip3" ]; then
    PIP="$VENV/bin/pip3"
    "$PIP" install --upgrade pip --quiet 2>/dev/null
    ok "pip aktuell (via pip3)"
else
    fail "pip nicht im venv gefunden!"
    exit 1
fi

# Dependencies installieren (mit Marker um unnoetige Reinstalls zu vermeiden)
REQ_FILE="$SCRIPT_DIR/requirements.txt"
MARKER="$VENV/.soma_installed"
if [ ! -f "$MARKER" ] || [ "$REQ_FILE" -nt "$MARKER" ]; then
    echo -e "  ${Y}…${NC} Installiere Python-Abhaengigkeiten (requirements.txt)..."
    if "$PIP" install -r "$REQ_FILE" 2>&1 | tail -20; then
        touch "$MARKER"
        ok "Alle Python-Abhaengigkeiten installiert"
    else
        warn "Einige Pakete hatten Probleme – pruefen:"
        warn "  $PIP install -r $REQ_FILE"
    fi
else
    ok "Python-Abhaengigkeiten aktuell (requirements.txt unveraendert)"
fi

# Schnell-Check der kritischen Imports
echo -e "  Pruefe kritische Python-Module..."
IMPORT_ERRORS=""
for mod in fastapi uvicorn redis pydantic django psutil structlog httpx aiomqtt numpy; do
    if ! "$PYTHON" -c "import $mod" 2>/dev/null; then
        IMPORT_ERRORS="$IMPORT_ERRORS $mod"
    fi
done
if [ -n "$IMPORT_ERRORS" ]; then
    warn "Fehlende Module:${R}$IMPORT_ERRORS${NC}"
    warn "Versuche Nachinstallation..."
    "$PIP" install -r "$REQ_FILE" --quiet 2>&1
else
    ok "Alle kritischen Python-Module verfuegbar"
fi

# ============================================================================
# Phase 4: .env Konfiguration
# ============================================================================
hdr "Phase 4/10: Konfiguration (.env)"

ENV_CREATED_NOW=0

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    if [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        ENV_CREATED_NOW=1
        warn ".env wurde aus .env.example erstellt"
    else
        # .env.example existiert auch nicht – Minimale .env erzeugen
        cat > "$SCRIPT_DIR/.env" <<'ENVEOF'
# ============================================================================
# SOMA-AI – Automatisch generierte .env (BITTE ANPASSEN!)
# ============================================================================
POSTGRES_DB=soma_db
POSTGRES_USER=soma
POSTGRES_PASSWORD=CHANGE_ME_strong_password
REDIS_HOST=localhost
REDIS_PORT=6379
MQTT_HOST=localhost
MQTT_PORT=1883
OLLAMA_HOST=http://localhost
OLLAMA_PORT=11434
OLLAMA_HEAVY_MODEL=qwen3:8b
OLLAMA_LIGHT_MODEL=qwen3:1.7b
BRAIN_CORE_HOST=0.0.0.0
BRAIN_CORE_PORT=8100
DJANGO_SECRET_KEY=CHANGE_ME_use_python_secrets_token_hex_50
DJANGO_DEBUG=True
DJANGO_PORT=8200
GITHUB_TOKEN=
HA_URL=http://homeassistant.local:8123
HA_TOKEN=
ENVEOF
        ENV_CREATED_NOW=1
        warn ".env wurde mit Defaults generiert"
    fi
fi

# .env laden
set -a
source "$SCRIPT_DIR/.env"
set +a

# Pruefen ob Defaults noch drin sind
ENV_NEEDS_EDIT=0
if grep -q "CHANGE_ME" "$SCRIPT_DIR/.env" 2>/dev/null; then
    ENV_NEEDS_EDIT=1
fi

if [ $ENV_CREATED_NOW -eq 1 ] || [ $ENV_NEEDS_EDIT -eq 1 ]; then
    echo ""
    echo -e "  ${R}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "  ${R}║${NC}  ${B}WICHTIG: .env-Datei muss noch angepasst werden!${NC}            ${R}║${NC}"
    echo -e "  ${R}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "  ${R}║${NC}                                                              ${R}║${NC}"
    echo -e "  ${R}║${NC}  Mindestens diese Werte aendern:                             ${R}║${NC}"
    echo -e "  ${R}║${NC}    ${Y}POSTGRES_PASSWORD${NC} = sicheres Passwort                    ${R}║${NC}"
    echo -e "  ${R}║${NC}    ${Y}DJANGO_SECRET_KEY${NC} = python3 -c 'import secrets;          ${R}║${NC}"
    echo -e "  ${R}║${NC}                        print(secrets.token_hex(50))'          ${R}║${NC}"
    echo -e "  ${R}║${NC}                                                              ${R}║${NC}"
    echo -e "  ${R}║${NC}  Optional aber empfohlen:                                    ${R}║${NC}"
    echo -e "  ${R}║${NC}    ${Y}GITHUB_TOKEN${NC}      = fuer Plugin-Generierung               ${R}║${NC}"
    echo -e "  ${R}║${NC}    ${Y}HA_TOKEN${NC}           = fuer Home Assistant Integration       ${R}║${NC}"
    echo -e "  ${R}║${NC}                                                              ${R}║${NC}"
    echo -e "  ${R}║${NC}  Datei: ${C}$SCRIPT_DIR/.env${NC}                ${R}║${NC}"
    echo -e "  ${R}║${NC}  Nach dem Bearbeiten: ${C}./start_soma.sh${NC} erneut starten     ${R}║${NC}"
    echo -e "  ${R}║${NC}                                                              ${R}║${NC}"
    echo -e "  ${R}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    if [ $ENV_CREATED_NOW -eq 1 ]; then
        echo -e "  ${Y}SOMA startet jetzt mit Default-Werten (funktioniert lokal).${NC}"
        echo -e "  ${Y}Fuer Production: .env bearbeiten und neu starten.${NC}"
        echo ""
        read -r -p "  Weiter mit Defaults? (J/n) " ans
        if [ "$ans" = "n" ] || [ "$ans" = "N" ]; then
            echo -e "\n  Bearbeite .env und starte dann erneut: ${C}./start_soma.sh${NC}\n"
            exit 0
        fi
    fi
fi

# Wenn POSTGRES_PASSWORD noch auf CHANGE_ME steht: Auto-generieren fuer lokalen Betrieb
if grep -q "^POSTGRES_PASSWORD=CHANGE_ME" "$SCRIPT_DIR/.env" 2>/dev/null; then
    AUTO_PG_PASS=$("$SYS_PYTHON" -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || echo "soma_auto_$(date +%s)")
    sed -i "s/^POSTGRES_PASSWORD=CHANGE_ME.*/POSTGRES_PASSWORD=$AUTO_PG_PASS/" "$SCRIPT_DIR/.env"
    ok "POSTGRES_PASSWORD auto-generiert (lokal sicher)"
    # Neu laden
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

if grep -q "^DJANGO_SECRET_KEY=CHANGE_ME" "$SCRIPT_DIR/.env" 2>/dev/null; then
    AUTO_DJ_KEY=$("$SYS_PYTHON" -c "import secrets; print(secrets.token_hex(50))" 2>/dev/null || echo "soma-auto-key-$(date +%s)")
    sed -i "s/^DJANGO_SECRET_KEY=CHANGE_ME.*/DJANGO_SECRET_KEY=$AUTO_DJ_KEY/" "$SCRIPT_DIR/.env"
    ok "DJANGO_SECRET_KEY auto-generiert"
    set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# ── Modelle aus .env oder Defaults ───────────────────────────────────────
OLLAMA_HEAVY="${OLLAMA_HEAVY_MODEL:-qwen3:8b}"
OLLAMA_LIGHT="${OLLAMA_LIGHT_MODEL:-qwen3:1.7b}"
OLLAMA_EMBED="nomic-embed-text"

# ── Ports ────────────────────────────────────────────────────────────────
BRAIN_PORT="${BRAIN_CORE_PORT:-8100}"
DJANGO_PORT_NUM="${DJANGO_PORT:-8200}"

# ============================================================================
# Phase 5: Mosquitto-Config & data-Verzeichnisse
# ============================================================================
hdr "Phase 5/10: Verzeichnisse & Configs"

# Datenverzeichnisse anlegen
mkdir -p "$SCRIPT_DIR/data/phone_recordings" "$SCRIPT_DIR/data/phone_sounds"
mkdir -p "$SCRIPT_DIR/mosquitto/config"
mkdir -p "$SCRIPT_DIR/evolution_lab/generated_plugins"
mkdir -p "$SCRIPT_DIR/evolution_lab/sandbox_env"

# Mosquitto-Config erstellen falls nicht vorhanden
MOSQUITTO_CONF="$SCRIPT_DIR/mosquitto/config/mosquitto.conf"
if [ ! -f "$MOSQUITTO_CONF" ]; then
    cat > "$MOSQUITTO_CONF" <<'MQTTEOF'
# SOMA-AI Mosquitto Configuration
listener 1883
protocol mqtt

listener 9001
protocol websockets

allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
MQTTEOF
    ok "mosquitto.conf erstellt"
else
    ok "mosquitto.conf vorhanden"
fi

# Leere Erinnerungsdateien anlegen
if [ ! -f "$SCRIPT_DIR/data/soma_memory.json" ]; then
    echo "[]" > "$SCRIPT_DIR/data/soma_memory.json"
    ok "soma_memory.json angelegt"
fi

if [ ! -f "$SCRIPT_DIR/data/consciousness_state.json" ]; then
    cat > "$SCRIPT_DIR/data/consciousness_state.json" <<'CSEOF'
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
CSEOF
    ok "consciousness_state.json angelegt"
fi

ok "Alle Verzeichnisse & Configs bereit"

# ============================================================================
# Phase 6: Cleanup & Docker-Container starten
# ============================================================================
hdr "Phase 6/10: Cleanup & Infrastruktur"

# Log-Rotation
rotate_log "$LOGDIR/brain_core.log" 5000
rotate_log "$LOGDIR/django.log" 5000

# Stale PID-Files aufraeumen
for pidfile in "$PIDDIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    PID=$(cat "$pidfile" 2>/dev/null)
    if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$pidfile"
    fi
done

# Zombie-Prozesse auf unseren Ports pruefen
if lsof -t -i:"$BRAIN_PORT" >/dev/null 2>&1; then
    if ! curl -sf --max-time 2 "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
        warn "Zombie auf Port $BRAIN_PORT – wird beendet"
        kill_port "$BRAIN_PORT"
    else
        echo -e "\n  ${G}SOMA-AI laeuft bereits und ist gesund!${NC}"
        echo -e "  Status: ${C}./start_soma.sh --status${NC}"
        echo -e "  Stoppen: ${C}./stop_all.sh${NC}\n"
        exit 0
    fi
fi

if lsof -t -i:"$DJANGO_PORT_NUM" >/dev/null 2>&1; then
    if ! curl -sf --max-time 2 "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1; then
        warn "Zombie auf Port $DJANGO_PORT_NUM – wird beendet"
        kill_port "$DJANGO_PORT_NUM"
    fi
fi

ok "Cleanup abgeschlossen"

# ── Docker-Container starten ────────────────────────────────────────────
# Pruefen ob Docker ueberhaupt verfuegbar ist
DOCKER_AVAILABLE=0
if docker info &>/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
elif sudo docker info &>/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
    # docker via sudo wrappen falls noch nicht geschehen
    if ! docker info &>/dev/null 2>&1; then
        _real_docker=$(command -v docker)
        docker() { sudo "$_real_docker" "$@"; }
        export -f docker
    fi
fi

if [ $DOCKER_AVAILABLE -eq 1 ]; then
    echo -e "\n  ${B}Docker-Container starten...${NC}"

    DOCKER_SERVICES="postgres redis mosquitto"

    # Asterisk nur wenn Image + SIP-Credentials vorhanden
    if docker image inspect soma-asterisk &>/dev/null 2>&1; then
        if [ -n "${VODAFONE_SIP_HOST:-}" ] && [ "${VODAFONE_SIP_USER:-}" != "CHANGE_ME_sip_username" ]; then
            DOCKER_SERVICES="$DOCKER_SERVICES asterisk"
            ok "Asterisk Phone Gateway wird mitgestartet"
        else
            warn "Asterisk-Image vorhanden, aber SIP-Credentials fehlen"
        fi
    fi

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
        MQTT_STATUS=$(docker inspect -f '{{.State.Status}}' soma-mosquitto 2>/dev/null || echo "none")
        [ "$MQTT_STATUS" = "running" ] && ok "Mosquitto: running" || warn "Mosquitto: $MQTT_STATUS"
    else
        warn "Services brauchen noch einen Moment (PG=$PG_OK, Redis=$REDIS_OK)"
    fi
else
    warn "Docker nicht verfuegbar – Container uebersprungen"
    warn "Starte Docker manuell: sudo systemctl enable --now docker.socket"
    warn "Dann: ./start_soma.sh erneut ausfuehren"
fi

# ============================================================================
# Phase 7: Ollama installieren & Modelle laden
# ============================================================================
hdr "Phase 7/10: Ollama (LLM Runtime)"

OLLAMA_STARTED=0

# Ollama installieren falls nicht vorhanden
if ! command -v ollama &>/dev/null; then
    echo -e "  ${Y}Ollama nicht gefunden – automatische Installation${NC}"
    echo -e "  ${Y}…${NC} Lade Ollama Installer..."

    if curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tail -5; then
        ok "Ollama installiert"
    else
        warn "Ollama auto-install fehlgeschlagen"
        warn "Manuell: https://ollama.com/download"
    fi
fi

# Ollama starten
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama laeuft bereits"
    OLLAMA_STARTED=1
else
    # Versuch 1: Systemd-Service
    if command -v ollama &>/dev/null; then
        if command -v systemctl &>/dev/null; then
            sudo systemctl start ollama 2>/dev/null || true
            sudo systemctl enable ollama 2>/dev/null || true
        else
            # Ohne systemd: direkt starten
            nohup ollama serve > "$LOGDIR/ollama.log" 2>&1 &
        fi

        for i in $(seq 1 20); do
            if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
                OLLAMA_STARTED=1
                break
            fi
            sleep 1
        done
        [ $OLLAMA_STARTED -eq 1 ] && ok "Ollama gestartet (System-Service)"
    fi

    # Versuch 2: Docker-Fallback (nur wenn Docker verfuegbar)
    if [ $OLLAMA_STARTED -eq 0 ] && [ $DOCKER_AVAILABLE -eq 1 ]; then
        warn "System-Ollama nicht verfuegbar – starte Docker-Container"
        docker compose -f "$SCRIPT_DIR/docker-compose.yml" up -d ollama 2>&1 | head -5
        for i in $(seq 1 30); do
            if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
                OLLAMA_STARTED=1
                break
            fi
            sleep 1
        done
        [ $OLLAMA_STARTED -eq 1 ] && ok "Ollama gestartet (Docker)"
    fi

    if [ $OLLAMA_STARTED -eq 0 ]; then
        fail "Ollama konnte nicht gestartet werden!"
        fail "  System: curl -fsSL https://ollama.com/install.sh | sh"
        fail "  Docker: docker compose up -d ollama"
    fi
fi

# Modelle pruefen und herunterladen
if [ $OLLAMA_STARTED -eq 1 ]; then
    MODELS=$(curl -sf http://localhost:11434/api/tags | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
print(', '.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "?")
    ok "Verfuegbare Modelle: ${C}$MODELS${NC}"

    REQUIRED_MODELS=("$OLLAMA_HEAVY" "$OLLAMA_LIGHT" "$OLLAMA_EMBED")
    for model in "${REQUIRED_MODELS[@]}"; do
        HAS_MODEL=$(curl -sf http://localhost:11434/api/tags | "$PYTHON" -c "
import sys,json
d=json.load(sys.stdin)
names=[m['name'] for m in d.get('models',[])]
target='$model'
found = target in names or any(n.startswith(target.split(':')[0]+':') for n in names)
print('yes' if found else 'no')" 2>/dev/null || echo "no")

        if [ "$HAS_MODEL" = "yes" ]; then
            ok "Modell vorhanden: ${C}$model${NC}"
        else
            warn "Modell fehlt: ${Y}$model${NC} – wird heruntergeladen..."
            echo -e "  ${C}(Dies kann beim ersten Start einige Minuten dauern)${NC}"
            ollama pull "$model" 2>&1 | tail -5
            [ $? -eq 0 ] && ok "Modell geladen: ${C}$model${NC}" || fail "Download fehlgeschlagen: $model"
        fi
    done

    # KV-Cache Warmup
    echo -e "  ${C}KV-Cache Warmup: Persona-Prefix vorladen...${NC}"
    WARMUP_RESPONSE=$(curl -sf --max-time 60 http://localhost:11434/api/chat -d "{
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
        ok "KV-Cache Warmup erfolgreich"
    else
        warn "KV-Cache Warmup fehlgeschlagen (nicht kritisch)"
    fi
else
    warn "Ollama nicht erreichbar – Modell-Check uebersprungen"
fi

# ============================================================================
# Phase 8: Django SSOT
# ============================================================================
hdr "Phase 8/10: Django SSOT (Port $DJANGO_PORT_NUM)"

# Datenbank-Strategie
PG_HEALTH=$(docker inspect -f '{{.State.Health.Status}}' soma-postgres 2>/dev/null || echo "none")
if [ "$PG_HEALTH" = "healthy" ]; then
    export USE_SQLITE=false
    ok "PostgreSQL healthy – Django nutzt PostgreSQL"
else
    export USE_SQLITE=true
    warn "PostgreSQL nicht verfuegbar – Django nutzt SQLite-Fallback"
fi

# Migrationen
echo -e "  Migrationen pruefen..."
cd "$SCRIPT_DIR"
MIGRATE_OUTPUT=$("$PYTHON" brain_memory_ui/manage.py migrate --run-syncdb --noinput 2>&1)
MIGRATE_RC=$?

if [ $MIGRATE_RC -eq 0 ]; then
    if echo "$MIGRATE_OUTPUT" | grep -q "Applying"; then
        APPLIED=$(echo "$MIGRATE_OUTPUT" | grep -c "Applying")
        ok "Migrationen angewandt: $APPLIED neue"
    else
        ok "Datenbank-Schema aktuell"
    fi
else
    warn "Migrationen fehlerhaft (RC=$MIGRATE_RC)"
    echo "$MIGRATE_OUTPUT" | tail -3 | while IFS= read -r line; do
        echo -e "    ${Y}$line${NC}"
    done
fi

# Django starten
if curl -sf "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1; then
    ok "Django laeuft bereits"
else
    nohup "$PYTHON" brain_memory_ui/manage.py runserver "0.0.0.0:$DJANGO_PORT_NUM" \
        > "$LOGDIR/django.log" 2>&1 &
    DJANGO_PID=$!
    echo "$DJANGO_PID" > "$PIDDIR/django.pid"

    if wait_for_url "http://localhost:$DJANGO_PORT_NUM/" 10; then
        ok "Django gestartet (PID $DJANGO_PID)"
    else
        warn "Django startet noch... (Log: .logs/django.log)"
        if [ -f "$LOGDIR/django.log" ]; then
            ERRORS=$(grep -i "error\|exception\|traceback" "$LOGDIR/django.log" 2>/dev/null | tail -3)
            [ -n "$ERRORS" ] && echo "$ERRORS" | while IFS= read -r line; do echo -e "    ${R}$line${NC}"; done
        fi
    fi
fi

# ============================================================================
# Phase 9: Brain Core
# ============================================================================
hdr "Phase 9/10: Brain Core (Port $BRAIN_PORT)"

if curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
    ok "Brain Core laeuft bereits"
else
    cd "$SCRIPT_DIR"
    nohup "$PYTHON" -m brain_core.main \
        > "$LOGDIR/brain_core.log" 2>&1 &
    BRAIN_PID=$!
    echo "$BRAIN_PID" > "$PIDDIR/brain_core.pid"

    echo -e "  Warte auf Brain Core (Ego, Voice, Memory, Discovery)..."
    BRAIN_OK=0
    for i in $(seq 1 60); do
        if curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1; then
            BRAIN_OK=1
            break
        fi

        if ! kill -0 "$BRAIN_PID" 2>/dev/null; then
            fail "Brain Core ist abgestuerzt!"
            echo -e "    ${R}Letzte Log-Zeilen:${NC}"
            tail -15 "$LOGDIR/brain_core.log" 2>/dev/null | while IFS= read -r line; do
                echo -e "    ${R}  $line${NC}"
            done
            break
        fi

        if [ $((i % 10)) -eq 0 ]; then
            LAST_LOG=$(tail -1 "$LOGDIR/brain_core.log" 2>/dev/null | head -c 80 || echo "...")
            echo -e "    ${C}${i}s – $LAST_LOG${NC}"
        fi
        sleep 1
    done

    if [ $BRAIN_OK -eq 1 ]; then
        ok "Brain Core gestartet (PID $BRAIN_PID)"
    elif kill -0 "$BRAIN_PID" 2>/dev/null; then
        warn "Brain Core braucht noch etwas (60s Timeout, Prozess laeuft weiter)"
        warn "  Log: tail -f .logs/brain_core.log"
    fi
fi

# ============================================================================
# Phase 10: First-Run Marker setzen & Zusammenfassung
# ============================================================================
hdr "Phase 10/10: Zusammenfassung"

# Marker setzen
touch "$FIRST_RUN_MARKER"

sleep 2

BOOT_END=$(date +%s)
BOOT_DURATION=$((BOOT_END - BOOT_START))

# Final Status
BRAIN_LIVE=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/health" >/dev/null 2>&1 && echo "1" || echo "0")
DJANGO_LIVE=$(curl -sf "http://localhost:$DJANGO_PORT_NUM/" >/dev/null 2>&1 && echo "1" || echo "0")
OLLAMA_LIVE=$(curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo "1" || echo "0")
PG_LIVE=$(docker inspect -f '{{.State.Health.Status}}' soma-postgres 2>/dev/null | grep -q healthy && echo "1" || echo "0")
REDIS_LIVE=$(docker inspect -f '{{.State.Health.Status}}' soma-redis 2>/dev/null | grep -q healthy && echo "1" || echo "0")

echo ""
if [ "$BRAIN_LIVE" = "1" ]; then
    VOICE_STATUS=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/voice" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
    EGO_STATUS=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/ego/snapshot" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "?")
    PLUGIN_COUNT=$(curl -sf "http://localhost:$BRAIN_PORT/api/v1/evolution/plugins" | "$PYTHON" -c "
import sys,json; d=json.load(sys.stdin); print(len(d.get('plugins',[])))" 2>/dev/null || echo "?")

    echo -e "${G}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${G}║${NC}  ${B}SOMA-AI ist online!${NC}                     (${BOOT_DURATION}s Boot-Zeit)  ${G}║${NC}"
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
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Soma hoert jetzt dauerhaft zu!${NC}                              ${G}║${NC}"
    echo -e "${G}║${NC}  ${Y}Sage \"Soma, ...\" um zu sprechen.${NC}                          ${G}║${NC}"
    echo -e "${G}║${NC}                                                              ${G}║${NC}"
    echo -e "${G}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${G}║${NC}  Status:  ${C}./start_soma.sh --status${NC}                         ${G}║${NC}"
    echo -e "${G}║${NC}  Logs:    ${C}./start_soma.sh --logs${NC}                           ${G}║${NC}"
    echo -e "${G}║${NC}  Stop:    ${C}./stop_all.sh${NC}                                    ${G}║${NC}"
    echo -e "${G}╚══════════════════════════════════════════════════════════════╝${NC}"

    # .env-Erinnerung falls noch Defaults drin sind
    if [ $ENV_NEEDS_EDIT -eq 1 ]; then
        echo ""
        echo -e "  ${Y}Erinnerung: .env enthaelt noch CHANGE_ME-Werte.${NC}"
        echo -e "  ${Y}Bearbeite ${C}$SCRIPT_DIR/.env${Y} und starte neu.${NC}"
    fi

    # Auto-open Dashboard
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
    echo -e "${Y}║${NC}  ${B}SOMA-AI startet noch...${NC}              (${BOOT_DURATION}s bisher)        ${Y}║${NC}"
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
    echo -e "${Y}║${NC}  Log pruefen: ${C}tail -f .logs/brain_core.log${NC}                  ${Y}║${NC}"
    echo -e "${Y}║${NC}  Status:      ${C}./start_soma.sh --status${NC}                      ${Y}║${NC}"
    echo -e "${Y}╚══════════════════════════════════════════════════════════════╝${NC}"

    if [ -f "$LOGDIR/brain_core.log" ]; then
        LAST_ERR=$(grep -i "error\|failed\|exception\|critical" "$LOGDIR/brain_core.log" 2>/dev/null | tail -5)
        if [ -n "$LAST_ERR" ]; then
            echo -e "\n  ${R}Letzte Fehler:${NC}"
            echo "$LAST_ERR" | while IFS= read -r line; do echo -e "    ${R}$line${NC}"; done
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
                echo -e "\n${Y}Restarting SOMA...${NC}\n"
                bash "$SCRIPT_DIR/stop_all.sh"
                sleep 2
                exec bash "$SCRIPT_DIR/start_soma.sh"
                ;;
            q|Q)
                echo -e "\n${Y}Stopping all services...${NC}\n"
                bash "$SCRIPT_DIR/stop_all.sh"
                exit 0
                ;;
            s|S)
                bash "$SCRIPT_DIR/start_soma.sh" --status
                echo ""
                ;;
            l|L)
                bash "$SCRIPT_DIR/start_soma.sh" --logs
                echo ""
                ;;
            *)
                echo -e "${R}Ungueltig. Nutze: r (restart), q (quit), s (status), l (logs)${NC}"
                ;;
        esac
    done
fi
