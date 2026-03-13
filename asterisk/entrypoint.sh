#!/bin/sh
# ================================================================
# SOMA Asterisk Phone Gateway — Entrypoint
# Generates pjsip.conf from template using env vars, then starts Asterisk.
# ================================================================
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   SOMA Phone Gateway — Asterisk 20       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Validate required environment variables
if [ -z "${VODAFONE_SIP_HOST}" ]; then
    echo "⚠  WARNING: VODAFONE_SIP_HOST nicht gesetzt."
    echo "   Trage in .env ein: VODAFONE_SIP_HOST=sipgate.de"
fi

if [ -z "${LINPHONE_SIP_PASS}" ]; then
    echo "⚠  WARNING: LINPHONE_SIP_PASS nicht gesetzt."
    echo "   Trage in .env ein: LINPHONE_SIP_PASS=dein_sicheres_passwort"
    echo "   Linphone-App: SIP-Adresse = sip:soma@<SOMA_IP>, Port 5060"
fi

# Auto-detect LAN IP for RTP/media routing (used in pjsip.conf)
if [ -z "${SOMA_LOCAL_IP}" ]; then
    SOMA_LOCAL_IP=$(hostname -I | awk '{print $1}')
    echo "ℹ  SOMA_LOCAL_IP auto-detected: ${SOMA_LOCAL_IP}"
fi
export SOMA_LOCAL_IP

# Generate pjsip.conf from template (substitutes ${VODAFONE_SIP_HOST} etc.)
if [ -f /etc/asterisk/pjsip.conf.tmpl ]; then
    envsubst < /etc/asterisk/pjsip.conf.tmpl > /etc/asterisk/pjsip.conf
    echo "✓ pjsip.conf generated (SIP host: ${VODAFONE_SIP_HOST:-NOT SET})"
else
    echo "✗ pjsip.conf.tmpl not found — PJSIP config missing!"
fi

# Ensure Asterisk dirs exist (Docker volume mounts may not have them)
mkdir -p /var/lib/asterisk/sounds/soma
mkdir -p /var/spool/asterisk/recording
mkdir -p /var/run/asterisk

echo "🚀 Starting Asterisk..."
echo ""

# Run Asterisk in foreground (f = foreground, p = priority)
exec /usr/sbin/asterisk -fp
