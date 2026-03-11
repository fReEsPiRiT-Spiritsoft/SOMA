/**
 * SOMA-AI Socket Client v3
 * ==========================
 * Real-time WebSocket-Verbindung zum brain_core.
 * Steuert das Visual Face basierend auf:
 *   - Thought-Events (STT, LLM, TTS, Emotion)
 *   - System-Metriken (CPU/RAM/VRAM/Load)
 *   - Audio-Level
 *
 * State Machine:
 *   idle -> listening -> thinking -> [bridge=thinking] -> speaking -> grace(8s) -> idle
 *   activeTurn=true blockiert STT-Noise waehrend thinking/speaking/grace
 */

(function () {
    'use strict';

    var BRAIN_WS_URL = 'ws://' + location.hostname + ':8100/ws/thinking';
    var BRAIN_API    = 'http://' + location.hostname + ':8100/api/v1';
    var statusBar    = document.getElementById('status-bar');
    var somaText     = document.getElementById('soma-text');

    var ws = null;
    var reconnectTimer = null;
    var heartbeatTimer = null;
    var activityTimeout = null;

    // -- Turn State --
    var ttsActive      = false;
    var pendingLLMText = false;
    var textHideTimer  = null;
    var textShownAt    = 0;
    var ttsEndedAt     = 0;
    var activeTurn     = false;
    var TEXT_GRACE_MS  = 1500;

    function resetActivityTimeout(ms) {
        if (ms === undefined) ms = 3000;
        if (activityTimeout) clearTimeout(activityTimeout);
        activityTimeout = setTimeout(function() {
            activeTurn  = false;
            ttsActive   = false;
            ttsEndedAt  = 0;
            hideText();
            if (window.SomaFace) window.SomaFace.setMode('idle');
        }, ms);
    }

    // == Connection Management ==

    function connect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;
        ws = new WebSocket(BRAIN_WS_URL);

        ws.onopen = function() {
            console.log('[SOMA Socket] Connected');
            setStatus('connected');
            heartbeatTimer = setInterval(function() {
                if (ws.readyState === WebSocket.OPEN) ws.send('ping');
            }, 30000);
            fetchVoiceStatus();
        };

        ws.onmessage = function(event) {
            try {
                var data = JSON.parse(event.data);
                if (data.type === 'thought') {
                    handleThought(data);
                } else {
                    handleMetrics(data);
                }
            } catch (e) {
                if (event.data === 'pong') return;
            }
        };

        ws.onclose = function() {
            console.log('[SOMA Socket] Disconnected');
            setStatus('disconnected');
            cleanup();
            scheduleReconnect();
        };

        ws.onerror = function() { ws.close(); };
    }

    function cleanup() {
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        heartbeatTimer = null;
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(function() {
            reconnectTimer = null;
            connect();
        }, 3000);
    }

    // == Thought Event Handler ==

    function handleThought(data) {
        var t = data.thought_type || '';
        var tag = (data.tag || '').toUpperCase();
        var extra = data.extra || {};

        if (!window.SomaFace) return;

        // -- STT: User spricht (oder Noise) --
        if (t === 'stt' || tag === 'STT') {
            // Waehrend SOMA denkt/spricht kommen staendig STT-Events
            // (Ambient-Noise, Echo). Diese MUESSEN ignoriert werden.
            if (activeTurn) return;

            window.SomaFace.setMode('listening');
            hideText();
            ttsActive      = false;
            pendingLLMText = false;
            ttsEndedAt     = 0;
            resetActivityTimeout(4000);
        }

        // -- LLM: Soma denkt --
        if (t === 'llm' || tag === 'LLM' || tag === 'THINKING') {
            activeTurn = true;
            window.SomaFace.setMode('thinking');
            resetActivityTimeout(60000);
            // Wenn die echte Antwort mitkommt, sofort Text anzeigen
            if (extra.response) {
                pendingLLMText = false;
                showText(extra.response, 0);
                ttsActive = true;
            }
        }

        // -- TTS: Soma spricht --
        // BRIDGE = Denkpause ("Moment...") -> Mode bleibt THINKING
        // Alles andere = echte Antwort -> Mode wird SPEAKING
        if (t === 'tts' || tag === 'TTS' || tag === 'SPEAKING' || tag === 'BRIDGE') {
            activeTurn = true;

            if (tag === 'BRIDGE') {
                // Bridge ist eine Denkpause. NUR thinking, NIEMALS speaking!
                window.SomaFace.setMode('thinking');
                ttsActive      = true;
                pendingLLMText = true;
                textShownAt    = Date.now();
            } else {
                // Echte Antwort wird gesprochen -> speaking
                window.SomaFace.setMode('speaking');
                pendingLLMText = false;
                var fullText = extra.response;
                if (fullText) {
                    showText(fullText, 0);
                    ttsActive = true;
                }
            }
        }

        // -- Emotion --
        if (t === 'emotion' || tag === 'EMOTION') {
            if (extra.stress !== undefined) {
                window.SomaFace.setEnergy(extra.stress);
            }
        }

        // -- Evolution Lab --
        if (tag === 'EVOLUTION' || tag === 'EVOLUTION_OK') {
            window.SomaFace.setMode('thinking');
            activeTurn = true;
            resetActivityTimeout(15000);
        }

        // -- Audio Level --
        if (extra.audio_level !== undefined) {
            window.SomaFace.pushAudioLevel(extra.audio_level);
        }
    }

    // == Metrics Handler ==

    function handleMetrics(metrics) {
        var loadLevel = metrics.load_level || 'idle';

        if (loadLevel === 'critical' && window.SomaFace) {
            window.SomaFace.setMode('critical');
        }

        var cpu  = metrics.cpu_percent  ? metrics.cpu_percent.toFixed(0)       : '--';
        var ram  = metrics.ram_percent  ? metrics.ram_percent.toFixed(0)       : '--';
        var vram = (metrics.gpu && metrics.gpu.vram_percent) ? metrics.gpu.vram_percent.toFixed(0) : '--';

        statusBar.textContent = 'SOMA | ' + loadLevel.toUpperCase() + ' | CPU ' + cpu + '% | RAM ' + ram + '% | VRAM ' + vram + '%';
    }

    // == Voice Status Polling (Fallback, alle 2s) ==

    async function fetchVoiceStatus() {
        try {
            var res = await fetch(BRAIN_API + '/voice');
            var v = await res.json();
            if (!v || v.status === 'offline') return;

            var s = v.stats || {};

            if (s.tts_speaking) {
                // TTS laeuft: Bridge = thinking, echte Antwort = speaking
                if (pendingLLMText) {
                    if (window.SomaFace) window.SomaFace.setMode('thinking');
                } else {
                    if (window.SomaFace) window.SomaFace.setMode('speaking');
                }
                ttsEndedAt  = 0;
                ttsActive   = true;
                activeTurn  = true;

            } else if (ttsActive) {
                // TTS hat aufgehoert

                if (pendingLLMText) {
                    // Bridge fertig, LLM denkt noch -> thinking
                    if (window.SomaFace) window.SomaFace.setMode('thinking');
                    return;
                }

                // Endzeit einmalig festhalten
                if (ttsEndedAt === 0) ttsEndedAt = Date.now();

                // Text bleibt TEXT_GRACE_MS sichtbar nach TTS-Ende
                if (Date.now() - ttsEndedAt < TEXT_GRACE_MS) return;

                // Grace abgelaufen -> komplett zuruecksetzen
                hideText();
                ttsEndedAt  = 0;
                ttsActive   = false;
                activeTurn  = false;
                if (window.SomaFace) window.SomaFace.setMode('idle');
            }
        } catch (e) { /* silent */ }
    }

    setInterval(fetchVoiceStatus, 2000);

    // == Status Display ==

    function setStatus(status) {
        var messages = {
            'connected':    'SOMA | Online',
            'disconnected': 'SOMA | Reconnecting...',
        };
        statusBar.textContent = messages[status] || 'SOMA';
    }

    // == Text Overlay ==

    function showText(text, duration) {
        if (textHideTimer) { clearTimeout(textHideTimer); textHideTimer = null; }
        if (!text) return;
        somaText.textContent = text;
        somaText.classList.add('visible');
        somaText.scrollTop = 0;
        textShownAt = Date.now();
        if (duration && duration > 0) {
            textHideTimer = setTimeout(hideText, duration);
        }
    }

    function hideText() {
        if (textHideTimer) { clearTimeout(textHideTimer); textHideTimer = null; }
        somaText.classList.remove('visible');
        ttsActive   = false;
        textShownAt = 0;
    }

    // == Public API ==

    window.SomaSocket = {
        showText: showText,

        sendCommand: function(command) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(command));
            }
        },

        getReadyState: function() {
            return ws ? ws.readyState : WebSocket.CLOSED;
        },
    };

    // == Auto-Connect ==
    connect();
    console.log('[SOMA Socket] Client v3 initialized.');
})();
