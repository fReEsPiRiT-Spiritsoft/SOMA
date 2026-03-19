#!/bin/sh
# ================================================================
# SOMA Asterisk Phone Gateway — Entrypoint
# Generates pjsip.conf from template using env vars, then starts Asterisk.
# Provider: Linhome Free SIP (sip.linhome.org)
# ================================================================
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   SOMA Phone Gateway — Asterisk (Linhome) ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Validate SIP Provider Credentials ────────────────────────────────
if [ -z "${VODAFONE_SIP_HOST}" ] || [ "${VODAFONE_SIP_HOST}" = "192.168.0.1" ]; then
    echo "⚠  WARNING: VODAFONE_SIP_HOST nicht korrekt gesetzt."
    echo "   Erwartet: VODAFONE_SIP_HOST=sip.linhome.org"
fi

if [ -z "${VODAFONE_SIP_USER}" ] || [ "${VODAFONE_SIP_USER}" = "soma" ]; then
    echo "⚠  WARNING: VODAFONE_SIP_USER nicht gesetzt."
    echo "   Erwartet: VODAFONE_SIP_USER=pattyhomeserver"
fi

if [ -z "${VODAFONE_SIP_PASS}" ] || echo "${VODAFONE_SIP_PASS}" | grep -q "HIER_DEIN"; then
    echo "╔══════════════════════════════════════════════════╗"
    echo "║  ⛔  VODAFONE_SIP_PASS fehlt!                     ║"
    echo "║  Trage dein Linhome-Passwort in .env ein:         ║"
    echo "║  VODAFONE_SIP_PASS=dein_linhome_passwort          ║"
    echo "╚══════════════════════════════════════════════════╝"
fi

if [ -z "${LINPHONE_SIP_PASS}" ]; then
    echo "ℹ  LINPHONE_SIP_PASS nicht gesetzt (optional, für lokalen SIP-Client)."
fi

# ── Auto-detect LAN IP for RTP/media routing ─────────────────────────
if [ -z "${SOMA_LOCAL_IP}" ]; then
    SOMA_LOCAL_IP=$(hostname -I | awk '{print $1}')
    echo "ℹ  SOMA_LOCAL_IP auto-detected: ${SOMA_LOCAL_IP}"
fi
export SOMA_LOCAL_IP

# ── Auto-detect PUBLIC IP for NAT traversal ──────────────────────────
# Linhome braucht die öffentliche IP im Contact-Header damit
# eingehende INVITEs durch den Router ankommen.
if [ -z "${SOMA_PUBLIC_IP}" ]; then
    SOMA_PUBLIC_IP=$(wget -qO- http://ifconfig.me 2>/dev/null || wget -qO- http://api.ipify.org 2>/dev/null || echo "")
    if [ -n "${SOMA_PUBLIC_IP}" ]; then
        echo "✓ SOMA_PUBLIC_IP auto-detected: ${SOMA_PUBLIC_IP}"
    else
        echo "⚠  Konnte öffentliche IP nicht ermitteln — nutze LAN-IP als Fallback"
        SOMA_PUBLIC_IP="${SOMA_LOCAL_IP}"
    fi
fi
export SOMA_PUBLIC_IP

# ── Generate pjsip.conf from template ────────────────────────────────
if [ -f /etc/asterisk/pjsip.conf.tmpl ]; then
    envsubst < /etc/asterisk/pjsip.conf.tmpl > /etc/asterisk/pjsip.conf
    echo "✓ pjsip.conf generated"
    echo "  SIP Host:   ${VODAFONE_SIP_HOST:-NOT SET}"
    echo "  SIP User:   ${VODAFONE_SIP_USER:-NOT SET}"
    echo "  Local IP:   ${SOMA_LOCAL_IP}"
else
    echo "✗ pjsip.conf.tmpl not found — PJSIP config missing!"
fi

# ── Patch ARI password (falls Variable gesetzt) ──────────────────────
if [ -n "${ASTERISK_ARI_PASS}" ] && [ -f /etc/asterisk/ari.conf ]; then
    sed -i "s|^password = .*|password = ${ASTERISK_ARI_PASS}|" /etc/asterisk/ari.conf
    echo "✓ ari.conf password patched"
fi

# ── Ensure Asterisk dirs exist ────────────────────────────────────────
mkdir -p /var/lib/asterisk/sounds/soma
mkdir -p /var/spool/asterisk/recording
mkdir -p /var/run/asterisk

echo ""
echo "🚀 Starting Asterisk (SIP → ${VODAFONE_SIP_HOST:-???})..."
echo ""

# Run Asterisk in foreground
exec /usr/sbin/asterisk -fp
