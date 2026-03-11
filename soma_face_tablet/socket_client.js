/**
 * SOMA-AI Socket Client v2
 * ==========================
 * Real-time WebSocket-Verbindung zum brain_core.
 * Steuert das Visual Face basierend auf:
 *   - Thought-Events (STT, LLM, TTS, Emotion)
 *   - System-Metriken (CPU/RAM/VRAM/Load)
 *   - Audio-Level
 */

(function () {
    'use strict';

    const BRAIN_WS_URL = `ws://${location.hostname}:8100/ws/thinking`;
    const BRAIN_API    = `http://${location.hostname}:8100/api/v1`;
    const statusBar    = document.getElementById('status-bar');
    const somaText     = document.getElementById('soma-text');

    let ws = null;
    let reconnectTimer = null;
    let heartbeatTimer = null;

    // Timeout um nach dem letzten Speaking/Thinking Event zurück zu idle zu fallen
    let activityTimeout = null;

    // TTS-Text: aktiv solange Soma spricht
    let ttsActive   = false;
    let textHideTimer = null;

    function resetActivityTimeout(ms = 3000) {
        if (activityTimeout) clearTimeout(activityTimeout);
        activityTimeout = setTimeout(() => {
            if (window.SomaFace) window.SomaFace.setMode('idle');
        }, ms);
    }

    // ── Connection Management ───────────────────────────────────────────

    function connect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        ws = new WebSocket(BRAIN_WS_URL);

        ws.onopen = () => {
            console.log('[SOMA Socket] Connected to brain_core');
            setStatus('connected');

            // Heartbeat
            heartbeatTimer = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) ws.send('ping');
            }, 30000);

            // Initialdaten holen
            fetchVoiceStatus();
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.type === 'thought') {
                    handleThought(data);
                } else {
                    // Metriken (health)
                    handleMetrics(data);
                }
            } catch (e) {
                if (event.data === 'pong') return;
            }
        };

        ws.onclose = () => {
            console.log('[SOMA Socket] Disconnected');
            setStatus('disconnected');
            cleanup();
            scheduleReconnect();
        };

        ws.onerror = () => { ws.close(); };
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

    // ── Thought Event Handler ───────────────────────────────────────────
    //  data: { type: "thought", thought_type: "stt"|"llm"|"tts"|...,
    //          content: "...", tag: "...", extra: {...} }

    function handleThought(data) {
        const t = data.thought_type || '';
        const tag = (data.tag || '').toUpperCase();
        const extra = data.extra || {};

        if (!window.SomaFace) return;

        // ── STT (Sprache erkannt → listening) ───────────────────
        if (t === 'stt' || tag === 'STT') {
            window.SomaFace.setMode('listening');
            hideText();                 // Alte Antwort wegblenden sobald User spricht
            ttsActive = false;
            resetActivityTimeout(4000);
        }

        // ── LLM (Soma denkt → thinking) ─────────────────────────
        if (t === 'llm' || tag === 'LLM' || tag === 'THINKING') {
            window.SomaFace.setMode('thinking');
            resetActivityTimeout(8000);  // Denken kann lange dauern
        }

        // ── TTS (Soma spricht → speaking) ───────────────────────
        if (t === 'tts' || tag === 'TTS' || tag === 'SPEAKING') {
            window.SomaFace.setMode('speaking');
            // Vollständiger Text aus extra.response (kein Truncating)
            const fullText = extra.response || data.content;
            if (fullText) {
                showText(fullText, 0);  // 0 = kein Auto-hide, bleibt bis TTS fertig
                ttsActive = true;
            }
            // Kein fester resetActivityTimeout — Polling erkennt TTS-Ende
        }

        // ── Emotion-Updates ─────────────────────────────────────
        if (t === 'emotion' || tag === 'EMOTION') {
            // Stress-Level als Energie-Boost
            if (extra.stress !== undefined) {
                window.SomaFace.setEnergy(extra.stress);
            }
        }

        // ── Evolution Lab ───────────────────────────────────────
        if (tag === 'EVOLUTION' || tag === 'EVOLUTION_OK') {
            window.SomaFace.setMode('thinking');
            resetActivityTimeout(5000);
        }

        // ── Audio-Level (wenn mitgeliefert) ─────────────────────
        if (extra.audio_level !== undefined) {
            window.SomaFace.pushAudioLevel(extra.audio_level);
        }
    }

    // ── Metrics Handler ─────────────────────────────────────────────────

    function handleMetrics(metrics) {
        const loadLevel = metrics.load_level || 'idle';

        // Nur als Fallback wenn gerade kein aktives Speaking/Thinking
        if (loadLevel === 'critical' && window.SomaFace) {
            window.SomaFace.setMode('critical');
        }

        // Status bar update
        const cpu  = metrics.cpu_percent?.toFixed(0)      || '—';
        const ram  = metrics.ram_percent?.toFixed(0)      || '—';
        const vram = metrics.gpu?.vram_percent?.toFixed(0) || '—';

        statusBar.textContent = `SOMA · ${loadLevel.toUpperCase()} · CPU ${cpu}% · RAM ${ram}% · VRAM ${vram}%`;
    }

    // ── Voice Status Polling (Fallback) ─────────────────────────────────

    async function fetchVoiceStatus() {
        try {
            const res = await fetch(`${BRAIN_API}/voice`);
            const v = await res.json();
            if (!v || v.status === 'offline') return;

            const s = v.stats || {};
            if (s.tts_speaking) {
                // Soma spricht gerade — sicherstellen dass Mode stimmt
                if (window.SomaFace) window.SomaFace.setMode('speaking');
            } else if (ttsActive) {
                // TTS war aktiv, ist jetzt fertig → Text ausblenden + zurück zu idle
                hideText();
                if (window.SomaFace) {
                    window.SomaFace.setMode('idle');
                    resetActivityTimeout(0);  // Sofort idle (kein extra Delay)
                }
            }
        } catch (e) { /* silent */ }
    }

    // Poll voice status alle 2s als Backup
    setInterval(fetchVoiceStatus, 2000);

    // ── Status Display ──────────────────────────────────────────────────

    function setStatus(status) {
        const messages = {
            'connected':    'SOMA · Online',
            'disconnected': 'SOMA · Reconnecting...',
        };
        statusBar.textContent = messages[status] || 'SOMA';
    }

    // ── Text Overlay ────────────────────────────────────────────────────

    // duration = 0  →  bleibt sichtbar bis hideText() gerufen wird
    // duration > 0  →  auto-fade nach X ms
    function showText(text, duration = 0) {
        if (textHideTimer) { clearTimeout(textHideTimer); textHideTimer = null; }
        somaText.textContent = text;
        somaText.classList.add('visible');
        somaText.scrollTop = 0;   // Anfang des Textes zeigen
        if (duration > 0) {
            textHideTimer = setTimeout(hideText, duration);
        }
    }

    function hideText() {
        if (textHideTimer) { clearTimeout(textHideTimer); textHideTimer = null; }
        somaText.classList.remove('visible');
        ttsActive = false;
    }

    // ── Public API ──────────────────────────────────────────────────────

    window.SomaSocket = {
        showText,

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

    console.log('[SOMA Socket] Client v2 initialized.');
})();
