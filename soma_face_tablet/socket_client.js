/**
 * SOMA-AI Socket Client
 * =======================
 * Real-time WebSocket-Verbindung zum brain_core.
 * Empfängt System-Metriken und steuert das Visual Face.
 */

(function () {
    'use strict';

    const BRAIN_WS_URL = `ws://${location.hostname}:8100/ws/thinking`;
    const statusBar = document.getElementById('status-bar');
    const somaText = document.getElementById('soma-text');

    let ws = null;
    let reconnectTimer = null;
    let heartbeatTimer = null;

    // ── Connection Management ───────────────────────────────────────────

    function connect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        ws = new WebSocket(BRAIN_WS_URL);

        ws.onopen = () => {
            console.log('[SOMA Socket] Connected to brain_core');
            setStatus('connected');

            // Heartbeat
            heartbeatTimer = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send('ping');
                }
            }, 30000);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleMetrics(data);
            } catch (e) {
                // Pong or non-JSON response
                if (event.data === 'pong') return;
            }
        };

        ws.onclose = () => {
            console.log('[SOMA Socket] Disconnected');
            setStatus('disconnected');
            cleanup();
            scheduleReconnect();
        };

        ws.onerror = (err) => {
            console.error('[SOMA Socket] Error:', err);
            ws.close();
        };
    }

    function cleanup() {
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        heartbeatTimer = null;
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 3000);
    }

    // ── Metrics Handler ─────────────────────────────────────────────────

    function handleMetrics(metrics) {
        const loadLevel = metrics.load_level || 'idle';

        // Map load level to face mode
        const modeMap = {
            'idle': 'idle',
            'normal': 'listening',
            'elevated': 'thinking',
            'high': 'thinking',
            'critical': 'critical',
        };

        const mode = modeMap[loadLevel] || 'idle';

        if (window.SomaFace) {
            window.SomaFace.setMode(mode);
        }

        // Status bar update
        const cpu = metrics.cpu_percent?.toFixed(0) || '—';
        const ram = metrics.ram_percent?.toFixed(0) || '—';
        const vram = metrics.gpu?.vram_percent?.toFixed(0) || '—';

        statusBar.textContent = `SOMA · ${loadLevel.toUpperCase()} · CPU ${cpu}% · RAM ${ram}% · VRAM ${vram}%`;
    }

    // ── Status Display ──────────────────────────────────────────────────

    function setStatus(status) {
        const messages = {
            'connected': 'SOMA · Online',
            'disconnected': 'SOMA · Reconnecting...',
        };
        statusBar.textContent = messages[status] || 'SOMA';
    }

    // ── Public API (for interaction from other scripts) ─────────────────

    window.SomaSocket = {
        showText(text, duration = 5000) {
            somaText.textContent = text;
            somaText.classList.add('visible');
            setTimeout(() => {
                somaText.classList.remove('visible');
            }, duration);
        },

        sendCommand(command) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(command));
            }
        },

        getReadyState() {
            return ws ? ws.readyState : WebSocket.CLOSED;
        },
    };

    // ── Auto-Connect ────────────────────────────────────────────────────
    connect();

    console.log('[SOMA Socket] Client initialized. 🔌');
})();
