# Asterisk wurde aus Debian Bookworm (12) entfernt.
# Debian Bullseye (11) enthält Asterisk 16 LTS — unterstützt ARI, PJSIP, alles.
FROM debian:bullseye-slim

ENV DEBIAN_FRONTEND=noninteractive

# Asterisk + gettext (for envsubst in entrypoint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    asterisk \
    ffmpeg \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Sound dirs for SOMA-generated TTS
RUN mkdir -p /var/lib/asterisk/sounds/soma \
             /var/spool/asterisk/recording \
             /var/spool/asterisk/tmp \
             /var/run/asterisk

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# SIP: 5060 UDP  |  ARI HTTP: 8088  |  RTP audio: 10000-10100
EXPOSE 5060/udp 5060/tcp 8088/tcp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
