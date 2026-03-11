/**
 * SOMA-AI Visual Face – Shader Logic v2
 * ========================================
 * Zwei schaltbare Visualisierungen:
 *   A) "Neural Wave" – Organische, lebendige Wellenlandschaft
 *   B) "Orb"         – Pulsierende Energie-Sphäre mit Tentakeln
 *
 * Reagiert auf Audio-Amplitude, Sprache, Denken, Emotion.
 * Kein Delta-Time → kein "Resetten" mehr.
 */

(function () {
    'use strict';

    // ══════════════════════════════════════════════════════════════════
    //  STATE
    // ══════════════════════════════════════════════════════════════════

    const state = {
        // Zielwerte (werden sanft interpoliert)
        amplitude:       0.0,
        targetAmplitude: 0.15,
        energy:          0.0,
        targetEnergy:    0.0,

        // Farbe (wird geblended)
        colorR: 0.0, colorG: 1.0, colorB: 0.53,
        targetR: 0.0, targetG: 1.0, targetB: 0.53,

        mode: 'idle',
        vizMode: 'wave',      // 'wave' | 'orb'
        speaking: false,
        audioLevel: 0.0,      // raw audio 0-1
        modeId:       0.0,    // interpolierter Modus-Index für Orb-Shader
        targetModeId: 0.0,    // Ziel  (0=idle 1=listen 2=speak 3=think 4=crit)
    };

    // ══════════════════════════════════════════════════════════════════
    //  THREE.JS SETUP
    // ══════════════════════════════════════════════════════════════════

    const canvas   = document.getElementById('soma-canvas');
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x000000, 1);

    const scene  = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
    camera.position.z = 1;

    // ══════════════════════════════════════════════════════════════════
    //  SHADER — NEURAL WAVE
    // ══════════════════════════════════════════════════════════════════

    const VERT = `
        varying vec2 vUv;
        void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
    `;

    const WAVE_FRAG = `
        precision highp float;

        uniform float u_time;
        uniform float u_amp;
        uniform float u_energy;
        uniform vec3  u_color;
        uniform vec2  u_res;

        varying vec2 vUv;

        // ── Simplex-ish noise (GPU) ─────────────────────────────
        vec3 mod289(vec3 x) { return x - floor(x / 289.0) * 289.0; }
        vec2 mod289(vec2 x) { return x - floor(x / 289.0) * 289.0; }
        vec3 permute(vec3 x) { return mod289((x * 34.0 + 1.0) * x); }

        float snoise(vec2 v) {
            const vec4 C = vec4(0.211324865, 0.366025403, -0.577350269, 0.024390243);
            vec2 i = floor(v + dot(v, C.yy));
            vec2 x0 = v - i + dot(i, C.xx);
            vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
            vec4 x12 = x0.xyxy + C.xxzz;
            x12.xy -= i1;
            i = mod289(i);
            vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
            vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
            m = m * m; m = m * m;
            vec3 x = 2.0 * fract(p * C.www) - 1.0;
            vec3 h = abs(x) - 0.5;
            vec3 ox = floor(x + 0.5);
            vec3 a0 = x - ox;
            m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
            vec3 g;
            g.x = a0.x * x0.x + h.x * x0.y;
            g.yz = a0.yz * x12.xz + h.yz * x12.yw;
            return 130.0 * dot(m, g);
        }

        // ── FBM (fractal brownian motion) ───────────────────────
        float fbm(vec2 p) {
            float v = 0.0, a = 0.5;
            mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
            for (int i = 0; i < 5; i++) {
                v += a * snoise(p);
                p  = rot * p * 2.0 + vec2(100.0);
                a *= 0.5;
            }
            return v;
        }

        void main() {
            float aspect = u_res.x / u_res.y;
            vec2 uv = vUv;
            uv.x *= aspect;

            float t = u_time;
            float amp = u_amp;
            float nrg = u_energy;

            // ── Mehrschichtige, organische Welle ────────────────
            float y = 0.5;  // Zentrierung

            // Hauptwelle: langsam, breit
            float w1 = sin(uv.x * 2.0 + t * 0.6) * 0.08 * amp;
            // Sekundaerwelle: mittel
            float w2 = sin(uv.x * 4.5 - t * 0.9 + 1.0) * 0.05 * amp;
            // Tertiaerwelle: schnell, fein
            float w3 = sin(uv.x * 8.0 + t * 1.4 + 2.5) * 0.025 * amp;
            // Noise-Welle: organisch, lebendig
            float wn = fbm(vec2(uv.x * 1.8 - t * 0.35, t * 0.15)) * 0.07 * amp;
            // Audio-Reaktion: schnelle Mikro-Textur
            float wa = snoise(vec2(uv.x * 12.0 + t * 3.0, t * 2.0)) * 0.04 * nrg;

            float wave = w1 + w2 + w3 + wn + wa;
            float dist = abs(uv.y - y - wave);

            // ── Glow-Layers ─────────────────────────────────────
            // Kern-Linie (duenn, hell)
            float core  = smoothstep(0.004, 0.0, dist);
            // Innerer Glow
            float glow1 = 0.006 / (dist + 0.006);
            // Aeusserer Glow (weich, weit)
            float glow2 = 0.04 / (dist * dist + 0.04);

            // Breathing Pulse
            float breath = 0.5 + 0.5 * sin(t * 0.4);
            float pulse  = 0.5 + 0.5 * sin(t * 1.2);

            // Farb-Komposition
            vec3 coreColor  = u_color * 1.8;
            vec3 glowColor  = u_color * (0.5 + nrg * 0.5);
            vec3 outerColor = u_color * 0.15;

            vec3 col = vec3(0.0);
            col += coreColor  * core;
            col += glowColor  * glow1 * (0.5 + 0.2 * breath);
            col += outerColor * glow2 * (0.4 + 0.15 * pulse);

            // ── Partikel-Feld (kleine leuchtende Punkte) ────────
            for (float i = 0.0; i < 8.0; i++) {
                float seed = i * 127.1 + 311.7;
                float px = fract(sin(seed) * 43758.5) * aspect;
                float py = 0.5 + sin(t * 0.3 + i * 2.0) * 0.15 * amp
                         + fbm(vec2(i, t * 0.2)) * 0.08;
                float pd = length(vec2(uv.x - px, uv.y - py));
                float brightness = amp * (0.3 + nrg * 0.7);
                float pt = 0.0008 / (pd * pd + 0.0008) * brightness;
                col += u_color * pt * 0.4;
            }

            // ── Horizontale Scan-Linien (subtil, CRT-Effekt) ───
            float scan = sin(uv.y * u_res.y * 1.5) * 0.015 + 0.985;
            col *= scan;

            // ── Vignette ────────────────────────────────────────
            vec2 vig = vUv * 2.0 - 1.0;
            float v = 1.0 - dot(vig * 0.45, vig * 0.45);
            v = smoothstep(0.0, 1.0, v);
            col *= v;

            // ── Hintergrund-Atem ────────────────────────────────
            float bgBreath = sin(t * 0.3) * 0.008 + 0.012;
            col += u_color * bgBreath * v;

            gl_FragColor = vec4(col, 1.0);
        }
    `;

    // ══════════════════════════════════════════════════════════════════
    //  SHADER — ORB (Energie-Sphaere)
    // ══════════════════════════════════════════════════════════════════

    const ORB_FRAG = `
        precision highp float;

        uniform float u_time;
        uniform float u_amp;
        uniform float u_energy;
        uniform vec3  u_color;
        uniform vec2  u_res;
        uniform float u_mode;   // 0=idle  1=listen  2=speak  3=think  4=crit

        varying vec2 vUv;

        // ── Simplex noise ───────────────────────────────────────
        vec3 mod289(vec3 x) { return x - floor(x / 289.0) * 289.0; }
        vec2 mod289(vec2 x) { return x - floor(x / 289.0) * 289.0; }
        vec3 permute(vec3 x) { return mod289((x * 34.0 + 1.0) * x); }

        float snoise(vec2 v) {
            const vec4 C = vec4(0.211324865, 0.366025403, -0.577350269, 0.024390243);
            vec2 i = floor(v + dot(v, C.yy));
            vec2 x0 = v - i + dot(i, C.xx);
            vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
            vec4 x12 = x0.xyxy + C.xxzz;
            x12.xy -= i1;
            i = mod289(i);
            vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
            vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
            m = m * m; m = m * m;
            vec3 x = 2.0 * fract(p * C.www) - 1.0;
            vec3 h = abs(x) - 0.5;
            vec3 ox = floor(x + 0.5);
            vec3 a0 = x - ox;
            m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
            vec3 g;
            g.x = a0.x * x0.x + h.x * x0.y;
            g.yz = a0.yz * x12.xz + h.yz * x12.yw;
            return 130.0 * dot(m, g);
        }

        float fbm(vec2 p) {
            float v = 0.0, a = 0.5;
            mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
            for (int i = 0; i < 6; i++) {
                v += a * snoise(p);
                p  = rot * p * 2.0 + vec2(100.0);
                a *= 0.5;
            }
            return v;
        }

        void main() {
            float aspect = u_res.x / u_res.y;
            vec2 uv = (vUv - 0.5) * 2.0;
            uv.x *= aspect;

            float t   = u_time;
            float amp = u_amp;
            float nrg = u_energy;

            // ── Mode weights (interpoliert ueber u_mode 0-4) ─────
            float mSpeak = smoothstep(1.5, 2.0, u_mode) - smoothstep(2.5, 3.0, u_mode);
            float mThink = smoothstep(2.5, 3.0, u_mode) - smoothstep(3.5, 4.0, u_mode);

            float dist  = length(uv);
            float angle = atan(uv.y, uv.x);

            // ── Kern-Radius mit organischer Verzerrung ──────────
            float baseR = 0.22 + amp * 0.07 + mSpeak * 0.02;
            float ds = 0.7 + mSpeak * 1.6 + mThink * 1.0;
            float distort = 0.0;
            distort += sin(angle * 3.0 + t * ds)       * 0.030 * amp;
            distort += sin(angle * 5.0 - t * (ds+0.4)) * 0.020 * amp;
            distort += snoise(vec2(angle * 2.0, t * (0.5 + mThink * 1.5))) * 0.040 * amp;
            distort += snoise(vec2(angle * 8.0, t * (3.0 + mSpeak * 4.0))) * 0.030 * nrg;

            float r = baseR + distort;

            // ── Kern ─────────────────────────────────────────────
            float inner = smoothstep(r + 0.01, r - 0.03, dist);

            float ps = 0.3 + mThink * 1.6 + mSpeak * 0.6;
            float plasma  = fbm(uv * 3.0 + t * ps) * 0.5 + 0.5;
            float plasma2 = fbm(uv * 5.0 - t * (ps * 0.85) + 50.0) * 0.5 + 0.5;
            float kernelTex = mix(plasma, plasma2, 0.5 + 0.5 * sin(t * (0.2 + mThink * 2.0)));

            // ── Basis-Glows ──────────────────────────────────────
            float glow1 = 0.030 / (abs(dist - r)        + 0.030);
            float glow2 = 0.150 / (dist * dist           + 0.150);
            float glow3 = 0.008 / (abs(dist - r - 0.05) + 0.008);

            // ── Speaking: 2 pulsierende Halo-Ringe ───────────────
            float haloP  = 0.5 + 0.5 * sin(t * 3.2);
            float speak1 = mSpeak * 0.045 / (abs(dist - r - 0.14) + 0.008);
            float speak2 = mSpeak * 0.025 / (abs(dist - r - 0.30 - haloP * 0.08) + 0.011);

            // ── Thinking: 2 neuronale Puls-Ringe (konzentrisch) ──
            float np1     = fract(dist * 8.0 - t * 5.0);
            float neural1 = mThink * smoothstep(0.80, 1.0, np1) * 0.20 * amp;
            float np2     = fract(dist * 5.0 - t * 7.5);
            float neural2 = mThink * smoothstep(0.86, 1.0, np2) * 0.13 * amp;

            // ── Tentakel / Strahlen (12 – wie Original) ──────────
            float rays   = 0.0;
            float raySpd = 0.12 + mSpeak * 0.50 + mThink * 0.65;
            float maxLen = 0.15 + amp * 0.22 + nrg * 0.12
                         + mSpeak * 0.12 + mThink * 0.18;
            for (float i = 0.0; i < 12.0; i++) {
                float ra  = i * 3.14159 * 2.0 / 12.0 + t * raySpd + sin(t * 0.3 + i) * 0.3;
                float rLen = maxLen + sin(t * (0.8 + mSpeak * 0.8) + i * 1.7) * 0.05;
                vec2  dir  = vec2(cos(ra), sin(ra));
                float proj = dot(uv, dir);
                if (proj > r * 0.5 && proj < r + rLen) {
                    vec2  cl   = dir * proj;
                    float d    = length(uv - cl);
                    float thick = 0.008 + 0.005 * sin(proj * 20.0 + t * 4.0)
                                + mSpeak * 0.004;
                    float ray  = thick / (d + thick);
                    ray *= smoothstep(r + rLen, r * 0.8, proj);
                    ray *= amp * (0.8 + mSpeak * 0.5 + mThink * 0.4);
                    rays += ray * 0.15;
                }
            }

            // ── Partikel (15 – wie Original) ─────────────────────
            float particles = 0.0;
            float partR = 0.40 + mSpeak * 0.30;
            for (float i = 0.0; i < 15.0; i++) {
                float seed = i * 73.1 + 137.3;
                float pa = fract(sin(seed) * 43758.5) * 6.283;
                float pr = 0.25 + fract(cos(seed) * 22578.3) * partR;
                float spd = 0.10 + fract(sin(seed * 2.0) * 1234.5)
                          * (0.2 + mSpeak * 0.35);
                pa += t * spd;
                pr += sin(t * 0.4 + i) * 0.08;
                vec2  pp = vec2(cos(pa), sin(pa)) * pr;
                float pd = length(uv - pp);
                float bright = 0.3 + nrg * 0.7;
                particles += (0.0004 / (pd * pd + 0.0004)) * bright * amp;
            }

            // ── Farb-Zusammenbau ─────────────────────────────────
            vec3 col = vec3(0.0);

            float kernBright = 0.6 + kernelTex * 0.6
                             + mSpeak * 0.35 + mThink * 0.20;
            col += u_color * kernBright * inner;

            col += u_color * 1.2 * glow1 * (0.25 + mSpeak * 0.40);
            col += u_color       * glow2  * (0.15 + (mSpeak + mThink) * 0.18) * amp;
            col += u_color * 0.6 * glow3  * 0.30;

            col += u_color * (speak1 + speak2);
            col += u_color * (neural1 + neural2);
            col += u_color * rays;
            col += u_color * 0.7 * particles;

            // Atemzug: tempo variiert mit Modus
            float bs    = 0.35 + mThink * 0.50 + mSpeak * 0.30;
            float breath = 0.5 + 0.5 * sin(t * bs);
            col *= 0.85 + 0.15 * breath;

            float scan = sin(vUv.y * u_res.y * 1.2) * 0.012 + 0.988;
            col *= scan;

            float vig = 1.0 - dot(vUv * 2.0 - 1.0, (vUv * 2.0 - 1.0) * 0.35);
            vig = smoothstep(0.0, 1.0, vig);
            col *= vig;

            gl_FragColor = vec4(col, 1.0);
        }
    `;

    // ══════════════════════════════════════════════════════════════════
    //  MATERIAL + MESHES
    // ══════════════════════════════════════════════════════════════════

    function makeUniforms() {
        return {
            u_time:   { value: 0.0 },
            u_amp:    { value: 0.15 },
            u_energy: { value: 0.0 },
            u_color:  { value: new THREE.Vector3(0.0, 1.0, 0.53) },
            u_res:    { value: new THREE.Vector2(window.innerWidth, window.innerHeight) },
            u_mode:   { value: 0.0 },
        };
    }

    const waveMat = new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: WAVE_FRAG,
        uniforms: makeUniforms(),
    });

    const orbMat = new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: ORB_FRAG,
        uniforms: makeUniforms(),
    });

    const geo = new THREE.PlaneGeometry(2, 2);

    const waveMesh = new THREE.Mesh(geo, waveMat);
    const orbMesh  = new THREE.Mesh(geo, orbMat);
    orbMesh.visible = false;

    scene.add(waveMesh);
    scene.add(orbMesh);

    // ══════════════════════════════════════════════════════════════════
    //  RESIZE
    // ══════════════════════════════════════════════════════════════════

    window.addEventListener('resize', () => {
        renderer.setSize(window.innerWidth, window.innerHeight);
        const w = window.innerWidth, h = window.innerHeight;
        waveMat.uniforms.u_res.value.set(w, h);
        orbMat.uniforms.u_res.value.set(w, h);
    });

    // ══════════════════════════════════════════════════════════════════
    //  MODE PRESETS
    // ══════════════════════════════════════════════════════════════════

    const MODES = {
        idle:      { amp: 0.30, energy: 0.00, r: 0.00, g: 1.00, b: 0.53, modeId: 0 },  // Grün  – ruhig
        listening: { amp: 0.50, energy: 0.20, r: 0.00, g: 0.85, b: 0.90, modeId: 1 },  // Cyan  – aktiv
        speaking:  { amp: 0.68, energy: 0.80, r: 0.00, g: 0.45, b: 1.00, modeId: 2 },  // BLAU  – energetisch
        thinking:  { amp: 0.60, energy: 0.55, r: 0.65, g: 0.00, b: 1.00, modeId: 3 },  // LILA  – neural
        critical:  { amp: 0.80, energy: 0.80, r: 1.00, g: 0.00, b: 0.15, modeId: 4 },  // Rot   – alarm
    };

    // ══════════════════════════════════════════════════════════════════
    //  PUBLIC API  →  window.SomaFace
    // ══════════════════════════════════════════════════════════════════

    window.SomaFace = {

        /** Setzt den Status-Modus (idle|listening|speaking|thinking|critical) */
        setMode(mode) {
            const p = MODES[mode] || MODES.idle;
            state.mode = mode;
            state.targetAmplitude = p.amp;
            state.targetEnergy    = p.energy;
            state.targetR         = p.r;
            state.targetG         = p.g;
            state.targetB         = p.b;
            state.targetModeId    = p.modeId;
        },

        /** Direkte Audio-Pegel Zufuehrung (0-1) */
        pushAudioLevel(level) {
            state.audioLevel = Math.max(0, Math.min(1, level));
            // Audio-Boost auf die Energie
            const base = MODES[state.mode]?.energy || 0;
            state.targetEnergy = base + level * 0.6;
            // Amplitude ebenfalls leicht anheben
            const baseAmp = MODES[state.mode]?.amp || 0.35;
            state.targetAmplitude = baseAmp + level * 0.3;
        },

        /** Umschalten Wave <-> Orb */
        toggleViz() {
            if (state.vizMode === 'wave') {
                state.vizMode = 'orb';
                waveMesh.visible = false;
                orbMesh.visible  = true;
            } else {
                state.vizMode = 'wave';
                waveMesh.visible = true;
                orbMesh.visible  = false;
            }
            // Persistieren: ueberleben Tab-Backgrounding / Page-Reload
            try { localStorage.setItem('soma_viz', state.vizMode); } catch(e) {}
            return state.vizMode;
        },

        /** Aktuelle Visualisierung abfragen */
        getVizMode() {
            return state.vizMode;
        },

        setAmplitude(v) { state.targetAmplitude = Math.max(0, Math.min(1.2, v)); },
        setEnergy(v)    { state.targetEnergy    = Math.max(0, Math.min(1.2, v)); },
    };

    // ══════════════════════════════════════════════════════════════════
    //  ANIMATION LOOP  (performance.now – keine Spruenge!)
    // ══════════════════════════════════════════════════════════════════

    let prevNow    = performance.now();
    let smoothTime = 0;

    function animate(now) {
        requestAnimationFrame(animate);

        // Delta mit Clamp: maximal 50ms (20 FPS minimum).
        // Verhindert "Spruenge" bei Tab-Wechsel komplett.
        let rawDelta = (now - prevNow) * 0.001;  // ms -> s
        prevNow = now;
        const delta = Math.min(rawDelta, 0.05);

        smoothTime += delta;

        // ── Sanfte Interpolation ────────────────────────────────
        const lerpSpeed  = 9.0;   // Amplitude/Energie
        const colorSpeed = 7.0;   // Farbe

        state.amplitude  += (state.targetAmplitude  - state.amplitude)  * delta * lerpSpeed;
        state.energy     += (state.targetEnergy     - state.energy)     * delta * lerpSpeed;
        state.modeId     += (state.targetModeId     - state.modeId)     * delta * lerpSpeed;
        state.colorR     += (state.targetR - state.colorR) * delta * colorSpeed;
        state.colorG     += (state.targetG - state.colorG) * delta * colorSpeed;
        state.colorB     += (state.targetB - state.colorB) * delta * colorSpeed;

        // ── Uniforms updaten ────────────────────────────────────
        const mat = (state.vizMode === 'wave') ? waveMat : orbMat;
        mat.uniforms.u_time.value   = smoothTime;
        mat.uniforms.u_amp.value    = state.amplitude;
        mat.uniforms.u_energy.value = state.energy;
        mat.uniforms.u_color.value.set(state.colorR, state.colorG, state.colorB);
        orbMat.uniforms.u_mode.value = state.modeId;

        renderer.render(scene, camera);
    }

    // ══════════════════════════════════════════════════════════════════
    //  START
    // ══════════════════════════════════════════════════════════════════

    window.SomaFace.setMode('idle');

    // Gespeicherten Visualisierungsmodus wiederherstellen (survives page reload)
    try {
        const savedViz = localStorage.getItem('soma_viz');
        if (savedViz === 'orb') {
            state.vizMode    = 'orb';
            waveMesh.visible = false;
            orbMesh.visible  = true;
            // Button-Label im DOM korrigieren (wird nach Skript-Laden gesetzt)
            window._somaRestoreViz = 'orb';
        }
    } catch(e) {}

    requestAnimationFrame(animate);

    console.log('[SOMA Face] Visual interface v2 initialized.');
})();
